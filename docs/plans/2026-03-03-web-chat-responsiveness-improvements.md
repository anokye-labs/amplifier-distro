# Web Chat UI Responsiveness Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate perceived lag in web chat UI by adding client-side placeholder and surfacing early server events.

**Architecture:** Add optimistic UI updates on the client side (placeholder thinking indicator) and surface execution:start event from orchestrator through translator to frontend for immediate feedback.

**Tech Stack:** JavaScript (Preact), Python (translator.py), event-driven WebSocket architecture

---

## Task 1: Add Client-Side Thinking Placeholder

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html:3587-3666` (sendMessage function)
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html:2869-2887` (content_start handler)
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html:2985-3005` (tool_call handler)
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html:3098-3114` (execution_error handler)

**Context:** Currently there's a gap between sending a message and seeing any response. Users see nothing until content_start arrives from the server. We'll add a client-side placeholder immediately after sending.

### Step 1: Add placeholder ID ref in ChatApp state

**Location:** After line 2533 in index.html (after other refs)

```javascript
const placeholderIdRef = useRef(null);
```

**Purpose:** Track the placeholder item ID so we can remove/replace it when real content arrives.

### Step 2: Modify sendMessage to add placeholder

**Location:** In sendMessage function, after line 3645 (after adding user message), before line 3646 (setExecuting)

**Find this code (lines 3642-3646):**
```javascript
// Normal message
setChronoItems(prev => [...prev, {
  id: makeId(), type: 'text', role: 'user',
  content, streaming: false, order: orderCounterRef.current++,
}]);
setExecuting(true);
```

**Replace with:**
```javascript
// Normal message
setChronoItems(prev => [...prev, {
  id: makeId(), type: 'text', role: 'user',
  content, streaming: false, order: orderCounterRef.current++,
}]);

// Add client-side placeholder for immediate feedback
const placeholderId = makeId();
placeholderIdRef.current = placeholderId;
setChronoItems(prev => [...prev, {
  id: placeholderId,
  type: 'thinking',
  content: '',
  streaming: true,
  order: orderCounterRef.current++,
}]);

setExecuting(true);
```

**Explanation:** Creates a streaming thinking block immediately to show the agent is processing.

### Step 3: Remove placeholder when content_start arrives

**Location:** In handleWsMessage switch, case 'content_start' (line 2869)

**Find this code (lines 2869-2887):**
```javascript
case 'content_start':
  if (msg.block_type === 'text') {
    const itemId = makeId();
    const localIdx = getLocalIndex(msg.index);
    blockMapRef.current['id-' + localIdx] = itemId;
    setChronoItems(prev => [...prev, {
      id: itemId, type: 'text', content: '', streaming: true,
      order: orderCounterRef.current++, role: 'assistant',
    }]);
  } else if (msg.block_type === 'thinking') {
    const itemId = makeId();
    const localIdx = getLocalIndex(msg.index);
    blockMapRef.current['thinking-id-' + localIdx] = itemId;
    setChronoItems(prev => [...prev, {
      id: itemId, type: 'thinking', content: '', streaming: true,
      order: orderCounterRef.current++,
    }]);
  }
  break;
```

**Replace with:**
```javascript
case 'content_start':
  // Remove client-side placeholder if present
  const placeholderId = placeholderIdRef.current;
  if (placeholderId) {
    setChronoItems(prev => prev.filter(item => item.id !== placeholderId));
    placeholderIdRef.current = null;
  }

  if (msg.block_type === 'text') {
    const itemId = makeId();
    const localIdx = getLocalIndex(msg.index);
    blockMapRef.current['id-' + localIdx] = itemId;
    setChronoItems(prev => [...prev, {
      id: itemId, type: 'text', content: '', streaming: true,
      order: orderCounterRef.current++, role: 'assistant',
    }]);
  } else if (msg.block_type === 'thinking') {
    const itemId = makeId();
    const localIdx = getLocalIndex(msg.index);
    blockMapRef.current['thinking-id-' + localIdx] = itemId;
    setChronoItems(prev => [...prev, {
      id: itemId, type: 'thinking', content: '', streaming: true,
      order: orderCounterRef.current++,
    }]);
  }
  break;
```

**Explanation:** Before adding the real content block, remove the placeholder to avoid duplication.

### Step 4: Remove placeholder if tool_call arrives first (edge case)

**Location:** In handleWsMessage switch, case 'tool_call' (line 2985)

**Find this code (line 2985):**
```javascript
case 'tool_call': {
```

**Add immediately after the opening brace (before existing code):**
```javascript
case 'tool_call': {
  // Remove client-side placeholder if tool call comes before content
  const placeholderId = placeholderIdRef.current;
  if (placeholderId) {
    setChronoItems(prev => prev.filter(item => item.id !== placeholderId));
    placeholderIdRef.current = null;
  }

  // ... existing tool_call handling code ...
```

**Explanation:** If the agent calls a tool immediately without text content, remove the placeholder.

### Step 5: Remove placeholder if execution_error arrives first (edge case)

**Location:** In handleWsMessage switch, case 'execution_error' (line 3098)

**Find this code (line 3098):**
```javascript
case 'execution_error':
  setSessions(prev => {
```

**Add immediately before setSessions:**
```javascript
case 'execution_error':
  // Remove client-side placeholder if error occurs immediately
  const placeholderId = placeholderIdRef.current;
  if (placeholderId) {
    setChronoItems(prev => prev.filter(item => item.id !== placeholderId));
    placeholderIdRef.current = null;
  }

  setSessions(prev => {
    // ... existing error handling code ...
```

**Explanation:** If execution fails immediately, remove the placeholder before showing error.

### Step 6: Clear placeholder ref on new session

**Location:** In newSession function (line 3700), after line 3728 (setPendingApproval)

**Find this code (around line 3728):**
```javascript
setPendingApproval(null);
```

**Add after:**
```javascript
setPendingApproval(null);
placeholderIdRef.current = null;
```

**Explanation:** Reset placeholder tracking when starting a new session.

### Step 7: Clear placeholder ref on session switch

**Location:** In switchSession function (line 4054), after line 4089 (setPendingApproval)

**Find this code (around line 4089):**
```javascript
setPendingApproval(null);
```

**Add after:**
```javascript
setPendingApproval(null);
placeholderIdRef.current = null;
```

**Explanation:** Reset placeholder tracking when switching between sessions.

### Step 8: Manual test

**Test procedure:**
1. Start the distro server: `cd ~/repo/amplifier-distro-heartbeat && make dev`
2. Open web chat in browser: `http://localhost:8000/apps/chat`
3. Send a message
4. **Expected:** Immediately see a collapsed thinking block with animated dots (placeholder)
5. **Expected:** When server responds with content_start, placeholder disappears and real content appears
6. Test edge case: Use a tool immediately (e.g., "read ~/test.txt")
7. **Expected:** Placeholder appears, then disappears when tool_call event arrives
8. Test edge case: Trigger an error (e.g., invalid command)
9. **Expected:** Placeholder appears, then disappears when error is shown

### Step 9: Commit

```bash
cd ~/repo/amplifier-distro-heartbeat
git add distro-server/src/amplifier_distro/server/apps/chat/static/index.html
git commit -m "feat(web-chat): add client-side thinking placeholder for immediate feedback"
```

---

## Task 2: Surface execution:start Event

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/translator.py:105-267` (translate method)
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html:2832-3161` (handleWsMessage switch)

**Context:** The orchestrator emits execution:start immediately after receiving the prompt, but translator drops it. We'll surface this event to show the user that processing has begun on the server side.

### Step 1: Add execution:start case to translator

**Location:** In translator.py, in the `translate` method match statement, after line 265 (before the default case)

**Find this code (line 266-267):**
```python
            case _:
                return None
```

**Add BEFORE the default case:**
```python
            case "execution:start":
                return {
                    "type": "execution_start",
                    "prompt": data.get("prompt", ""),
                }

            case "execution:end":
                return {
                    "type": "execution_end",
                }

            case _:
                return None
```

**Explanation:** Translates orchestrator's execution:start/end events to wire protocol messages.

### Step 2: Add execution_start handler in frontend

**Location:** In index.html handleWsMessage switch, after line 2834 (after 'auth_ok' case)

**Find this code (line 2833-2834):**
```javascript
      case 'auth_ok':
        break;
```

**Add after:**
```javascript
      case 'auth_ok':
        break;

      case 'execution_start':
        // Server has begun processing - replace placeholder with real server signal
        // This can arrive slightly after client placeholder in some cases
        if (isActiveStream) {
          setSessions(prev => {
            const next = new Map(prev);
            if (ownerKey && next.has(ownerKey)) {
              const s = next.get(ownerKey);
              next.set(ownerKey, { ...s, status: 'running' });
            }
            return next;
          });
        }
        break;

      case 'execution_end':
        // Execution completed (may be followed by prompt_complete)
        break;
```

**Explanation:** Updates session status to 'running' when server begins processing. The execution_end case is added for completeness but doesn't need action since prompt_complete already handles cleanup.

### Step 3: Manual test

**Test procedure:**
1. Start the distro server: `cd ~/repo/amplifier-distro-heartbeat && make dev`
2. Open browser DevTools Console
3. Send a message in web chat
4. **Expected:** See WebSocket message with `{"type": "execution_start", ...}` in Network tab
5. **Expected:** Session status updates to 'running' (check session card in left panel)
6. **Expected:** When execution completes, see `{"type": "execution_end"}` message

### Step 4: Commit

```bash
cd ~/repo/amplifier-distro-heartbeat
git add distro-server/src/amplifier_distro/server/apps/chat/translator.py
git add distro-server/src/amplifier_distro/server/apps/chat/static/index.html
git commit -m "feat(web-chat): surface execution:start and execution:end events for server feedback"
```

---

## Task 3: Comprehensive Event Audit

**Purpose:** Document ALL events emitted by the orchestrator, their current handling status, and recommendations for improving responsiveness.

### Event Audit Table

Based on analysis of:
- `~/.amplifier/cache/amplifier-module-loop-streaming-*/amplifier_module_loop_streaming/__init__.py`
- `distro-server/src/amplifier_distro/server/apps/chat/translator.py`
- `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`

| Event Name | When Emitted | Translator Case | Frontend Handler | Status | Recommendation |
|------------|--------------|----------------|------------------|--------|----------------|
| **prompt:submit** | Before orchestrator processes prompt (line 194) | MISSING | MISSING | DROPPED | LOW PRIORITY - happens synchronously with user send action |
| **execution:start** | After prompt submitted, before provider call (line 222) | MISSING | MISSING | DROPPED | **HIGH PRIORITY** - Add for immediate feedback (Task 2) |
| **execution:end** | After orchestrator loop completes (line 807) | MISSING | MISSING | DROPPED | MEDIUM PRIORITY - Add for lifecycle completeness (Task 2) |
| **provider:request** | Before each LLM call in loop (line 254) | MISSING | MISSING | DROPPED | LOW PRIORITY - too granular for UI, useful for logs only |
| **provider:error** | When LLM call fails (lines 417, 428, 786, 797) | MISSING | MISSING | DROPPED | **HIGH PRIORITY** - Surface for better error feedback |
| **content_block:start** | When content block begins (line 446 non-streaming, streaming via provider) | YES (line 111) | YES (line 2869) | HANDLED | ✅ Working correctly |
| **content_block:delta** | During streaming content (line 118, streaming via provider) | YES (line 118) | YES (line 2889) | HANDLED | ✅ Working correctly |
| **content_block:end** | When content block completes (line 456, streaming via provider) | YES (line 133) | YES (line 2919) | HANDLED | ✅ Working correctly |
| **thinking:delta** | During thinking block streaming (emitted by provider) | YES (line 140) | YES (line 2951) | HANDLED | ✅ Working correctly |
| **thinking:final** | When thinking completes (emitted by provider) | YES (line 146) | YES (line 2961) | HANDLED | ✅ Working correctly |
| **tool:pre** | Before tool execution (lines 966, 1108) | YES (line 152) | YES (line 2985) | HANDLED | ✅ Working correctly |
| **tool:post** | After tool execution (lines 1023, 1160) | YES (line 165) | YES (line 3007) | HANDLED | ✅ Working correctly |
| **tool:error** | When tool execution fails (lines 990, 1080) | YES (line 193) | YES (line 3007) | HANDLED | ✅ Working correctly |
| **delegate:agent_spawned** | When sub-agent is created (emitted by delegate tool) | YES (line 203) | YES (line 3018) | HANDLED | ✅ Working correctly |
| **orchestrator:complete** | After full turn completes (line 166) | YES (line 217) as "prompt_complete" | YES (line 3051) | HANDLED | ✅ Working correctly |
| **orchestrator:rate_limit_delay** | When rate limiting applies (line 115) | MISSING | MISSING | DROPPED | LOW PRIORITY - internal throttling detail |
| **orchestrator:iteration_start** | NOT EMITTED | N/A | N/A | N/A | Future enhancement if added |
| **cancel:requested** | When user requests cancellation (emitted by server) | YES (line 227) | YES (line 3132) | HANDLED | ✅ Working correctly |
| **cancel:completed** | When cancellation finishes (emitted by server) | YES (line 224) | YES (line 3088) | HANDLED | ✅ Working correctly |
| **display_message** | When hooks emit user-facing messages (emitted by hooks) | YES (line 230) | YES (line 3028) | HANDLED | ✅ Working correctly |
| **approval_request** | When hook requests approval (emitted by hooks) | YES (line 238) | YES (line 3037) | HANDLED | ✅ Working correctly |
| **llm:response** | After LLM call completes (emitted by providers) | YES (line 248) as "token_usage" | YES (line 2970) | HANDLED | ✅ Working correctly |
| **provider:post** | After provider call (emitted by providers) | YES (line 248) as "token_usage" | YES (line 2970) | HANDLED | ✅ Working correctly |
| **session:start** | When session begins (emitted by server, not orchestrator) | MISSING | YES (line 2836) as "session_created" | PARTIAL | Note: Different event name |
| **session:end** | When session ends (emitted by server, not orchestrator) | MISSING | MISSING | DROPPED | LOW PRIORITY - cleanup handled by ws:close |

### Priority Recommendations

**Immediate (High Value, Low Risk):**
1. ✅ **execution:start** - Implemented in Task 2 - provides immediate server-side feedback
2. **provider:error** - Surface LLM failures with user-friendly messages instead of silent retries

**Short Term (Medium Value):**
1. **execution:end** - ✅ Implemented in Task 2 - completes execution lifecycle visibility
2. Add error recovery UI for provider:error cases

**Low Priority (Nice to Have):**
1. **provider:request** - Only useful for debugging/advanced users
2. **orchestrator:rate_limit_delay** - Internal implementation detail
3. **session:end** - WebSocket close already handles cleanup

### Notes

**Event Categories:**
- **Lifecycle Events:** prompt:submit, execution:start/end, session:start/end → Framework coordination
- **Content Events:** content_block:*, thinking:* → Already well-handled with streaming
- **Tool Events:** tool:pre/post/error, delegate:agent_spawned → Already well-handled
- **Error Events:** provider:error, tool:error, execution_error → Need improvement
- **Control Events:** cancel:*, approval_request → Already well-handled
- **Metadata Events:** llm:response (token usage), orchestrator:rate_limit_delay → Informational only

**Key Insight:** The current implementation handles **content streaming** and **tool execution** very well. The gap is in **early lifecycle feedback** (execution:start) and **error surfacing** (provider:error). Tasks 1 and 2 address the lifecycle feedback gap.

---

## Testing Strategy

### Integration Test Checklist

After completing all tasks, verify:

1. **Normal Message Flow:**
   - [ ] Send message → see placeholder immediately
   - [ ] Placeholder disappears when real content starts
   - [ ] Content streams normally
   - [ ] Token usage appears after completion

2. **Tool Call Flow:**
   - [ ] Send "read ~/test.txt" → placeholder appears
   - [ ] Placeholder disappears when tool_call event arrives
   - [ ] Tool executes and result displays
   - [ ] No duplicate thinking blocks

3. **Error Flow:**
   - [ ] Trigger error (invalid command) → placeholder appears
   - [ ] Placeholder disappears when execution_error arrives
   - [ ] Error message displays correctly
   - [ ] Can send new messages after error

4. **Session Management:**
   - [ ] Switch sessions → no stale placeholders
   - [ ] Create new session → no stale placeholders
   - [ ] Resume history session → no placeholders injected

5. **Edge Cases:**
   - [ ] Fast response (content_start arrives very quickly)
   - [ ] Immediate tool call (no text content first)
   - [ ] Cancellation (Ctrl+C) → placeholder removed
   - [ ] Multiple rapid messages → placeholders track correctly

### Manual Verification Commands

```bash
# Start server
cd ~/repo/amplifier-distro-heartbeat
make dev

# Open browser
open http://localhost:8000/apps/chat

# Test commands
# 1. Normal: "Hello, how are you?"
# 2. Tool: "read ~/.gitconfig"
# 3. Error: "/invalid-command"
# 4. Fast: "Say 'hi' and nothing else"
# 5. Cancel: Send long request, then Ctrl+C (or Stop button)
```

---

## Success Criteria

- [ ] No perceived lag between sending message and seeing UI feedback
- [ ] Placeholder appears within 50ms of clicking Send
- [ ] execution:start event visible in Network tab
- [ ] No duplicate thinking blocks or orphaned placeholders
- [ ] All edge cases handled gracefully
- [ ] Session switching works without stale state
- [ ] Commits are atomic and well-described

---

## Future Enhancements (Out of Scope)

1. **provider:error surfacing** - Show user-friendly error messages for LLM failures
2. **Progressive loading states** - Show "Connecting to model..." vs "Processing..." vs "Streaming response..."
3. **Optimistic tool execution** - Show tool call immediately before server confirmation
4. **Typing indicator variants** - Different animations for thinking vs tool execution vs network wait
5. **Retry mechanism** - Allow user to retry failed requests without re-typing

