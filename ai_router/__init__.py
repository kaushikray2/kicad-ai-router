try:
    from .ai_router_plugin import AIRouterPlugin
    AIRouterPlugin().register()
except ImportError:
    # v1.11.1 (#76): outside KiCad's Python (unit tests, CI, backend
    # tooling) pcbnew/wx are absent -- the package's pure-Python
    # modules must stay importable. Inside KiCad the import succeeds
    # and the plugin registers exactly as before.
    pass
