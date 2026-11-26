# Copyright (C) 2026 SiliconBrane Inc.
#
# This file is part of KiCad AI Router.
#
# KiCad AI Router is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# KiCad AI Router is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with KiCad AI Router. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

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
