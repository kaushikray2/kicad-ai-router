"""Minimal OpenAI-compatible chat client with tool calling.

Uses urllib only (no third-party deps inside KiCad's Python).
Falls back to parsing a JSON action object out of the message content when
the endpoint/model does not return native tool_calls (the Rev I lesson:
accept the model's native output format instead of idling)."""
import json, re, urllib.request, urllib.error

class LLMError(Exception):
    pass

class LLMClient:
    def __init__(self, endpoint, model, api_key='', timeout_s=600):
        self.endpoint = endpoint.rstrip('/')
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    def chat(self, messages, tools=None):
        body = {'model': self.model, 'messages': messages}
        if tools:
            body['tools'] = tools
            body['tool_choice'] = 'auto'
        req = urllib.request.Request(
            self.endpoint + '/chat/completions',
            data=json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'kicad-ai-router/1.0'})
        if self.api_key:
            req.add_header('Authorization', 'Bearer ' + self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.URLError as e:
            raise LLMError('endpoint unreachable: %s' % e)
        except Exception as e:
            raise LLMError('request failed: %s' % e)
        try:
            msg = data['choices'][0]['message']
        except Exception:
            raise LLMError('unexpected response shape: %s' % json.dumps(data)[:300])
        calls = []
        for tc in (msg.get('tool_calls') or []):
            fn = tc.get('function') or {}
            args = fn.get('arguments') or '{}'
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            calls.append({'id': tc.get('id', ''), 'name': fn.get('name', ''), 'args': args})
        content = msg.get('content') or ''
        if not calls and content:
            # JSON-action fallback: find {"tool": ..., "args"/"arguments": ...}
            m = re.search(r'\{.*\}', content, re.S)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    if isinstance(obj, dict) and 'tool' in obj:
                        calls.append({'id': 'fallback', 'name': obj['tool'],
                                      'args': obj.get('args') or obj.get('arguments') or {}})
                except Exception:
                    pass
        return {'content': content, 'tool_calls': calls, 'raw': msg}
