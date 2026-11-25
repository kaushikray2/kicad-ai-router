# #140 (B14): importing this package for its modules (backend
# jobs, tests, tooling) must not run the GUI plugin registration
# -- in a headless context the plugin import can fail outright
# (no wx), which used to take every engineered module down with
# it.  Under KiCad's plugin loader the registration below runs
# exactly as before.
try:
    from .ai_router_plugin import AIRouterPlugin
    AIRouterPlugin().register()
except Exception:
    pass
