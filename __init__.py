# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

# 0. Early development bootstrap: ensure dev/lib (.so) is mounted on sys.path
try:
    from .dev import loader
    loader.setup_dev_environment()
except ImportError:
    pass


from . import bridge
from . import i18n
from . import operators
from . import ui


def register():
    # 1. Development environment & local native library mounting
    try:
        from . import dev
        dev.register()
    except ImportError:
        pass

    i18n.register()
    operators.register()
    ui.register()

    # Push the persisted thread budget into the native process policy once at load.
    try:
        from .bridge.engine import apply_thread_count
        from .bridge.prefs import get_prefs

        apply_thread_count(get_prefs())
    except Exception:
        pass


def unregister():
    ui.unregister()
    operators.unregister()
    i18n.unregister()

    try:
        from . import dev
        dev.unregister()
    except ImportError:
        pass


