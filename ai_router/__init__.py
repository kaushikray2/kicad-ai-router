try:
    from .ai_router_plugin import AIRouterPlugin
    AIRouterPlugin().register()
except ImportError as e:
    # v1.11.1 (#76): outside KiCad's Python (unit tests, CI, backend
    # tooling) pcbnew/wx are absent -- the package's pure-Python
    # modules must stay importable. Inside KiCad the import succeeds
    # and the plugin registers exactly as before.
    # v1.11.5 (#123): but SAY so -- a genuinely missing module (e.g.
    # a PCM zip that does not ship one) used to make the plugin
    # vanish from the toolbar with no message anywhere.
    print('ai_router: plugin not registered (%r)' % (e,))
