"""
A pure Python implementation of systemd's service notification protocol (sd_notify).
Imported from https://github.com/Liganic/python-sdnotify
"""

import socket
import os


class SystemdNotifier:
    """This class holds a connection to the systemd notification socket
    and can be used to send messages to systemd using its notify method."""

    def __init__(self, debug: bool = False):
        """Instantiate a new notifier object. This will initiate a connection
        to the systemd notification socket.

        Normally this method silently ignores exceptions (for example, if the
        systemd notification socket is not available) to allow applications to
        function on non-systemd based systems. However, setting debug=True will
        cause this method to raise any exceptions generated to the caller, to
        aid in debugging.
        """
        self.debug = debug
        try:
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            addr = os.getenv('NOTIFY_SOCKET')
            if addr[0] == '@':
                addr = '\0' + addr[1:]
            self.socket.connect(addr)
        except Exception:
            self.socket = None
            if self.debug:
                raise

    def notify(self, state: str):
        """Send a notification to systemd. state is a string; see
        the man page of sd_notify (https://www.freedesktop.org/software/systemd/man/sd_notify.html)
        for a description of the allowable values.

        Normally this method silently ignores exceptions (for example, if the
        systemd notification socket is not available) to allow applications to
        function on non-systemd based systems. However, setting debug=True will
        cause this method to raise any exceptions generated to the caller, to
        aid in debugging."""
        try:
            self.socket.sendall(state.encode())
        except Exception:
            if self.debug:
                raise
