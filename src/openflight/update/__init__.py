"""On-device updater: follow a release channel and stage releases beside the install.

``openflight-update`` (see :mod:`openflight.update.cli`) is run by a systemd
timer and by the kiosk launcher. The server only reads the status this
package writes and asks the launcher to restart by exiting with
:data:`RESTART_EXIT_CODE`.
"""

# openflight-server exits with this code to ask start-kiosk.sh to apply the
# staged release and relaunch; the launcher owns the swap, never the server.
RESTART_EXIT_CODE = 75

DEFAULT_REPOSITORY = "open-flight/openflight"
