import os
import time
import types
import logging
import traceback

import ftrack_api
from ayon_api import get_project_settings

from .ftrack_session import OPServerSession
from .lib import modules_from_path

"""
# Required - Needed for connection to Ftrack
FTRACK_SERVER # Ftrack server e.g. "https://myFtrack.ftrackapp.com"
FTRACK_API_KEY # Ftrack user's API key "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
FTRACK_API_USER # Ftrack username e.g. "user.name"

# Required - Needed for import included modules
PYTHONPATH # Path to ftrack_api and paths to all modules used in actions
    - path to ftrack_action_handler, etc.
"""


class FtrackServer:
    def __init__(self, handler_paths=None):
        """
            - 'type' is by default set to 'action' - Runs Action server
            - enter 'event' for Event server

            EXAMPLE FOR EVENT SERVER:
                ...
                server = FtrackServer()
                server.run_server()
                ..
        """

        # set Ftrack logging to Warning only - OPTIONAL
        ftrack_log = logging.getLogger("ftrack_api")
        ftrack_log.setLevel(logging.WARNING)

        self.log = logging.getLogger(__name__)

        self.stopped = True
        self.is_running = False

        self.handler_paths = handler_paths or []

    def stop_session(self):
        self.stopped = True
        if self.session.event_hub.connected is True:
            self.session.event_hub.disconnect()
        self.session.close()
        self.session = None

    def set_files(self, paths):
        # Iterate all paths
        register_functions = []
        for path in paths:
            # Try to format path with environments
            try:
                path = path.format(**os.environ)
            except BaseException:
                pass

            # Get all modules with functions
            modules, crashed = modules_from_path(path)
            for filepath, exc_info in crashed:
                self.log.warning("Filepath load crashed {}.\n{}".format(
                    filepath, "".join(traceback.format_exception(*exc_info))
                ))

            for filepath, module in modules:
                register_function = None
                for name, attr in module.__dict__.items():
                    if (
                        name == "register"
                        and isinstance(attr, types.FunctionType)
                    ):
                        register_function = attr
                        break

                if not register_function:
                    self.log.warning(
                        "\"{}\" - Missing register method".format(filepath)
                    )
                    continue

                register_functions.append(
                    (filepath, register_function)
                )

        if not register_functions:
            self.log.warning((
                "There are no events with `register` function"
                " in registered paths: \"{}\""
            ).format("| ".join(paths)))

        for filepath, register_func in register_functions:
            try:
                register_func(self.session)
            except Exception:
                self.log.warning(
                    "\"{}\" - register was not successful".format(filepath),
                    exc_info=True
                )

    def set_handler_paths(self, paths):
        self.handler_paths = paths
        if self.is_running:
            self.stop_session()
            self.run_server()

        elif not self.stopped:
            self.run_server()

    def run_server(self, session=None, load_files=True):
        self.stopped = False
        self.is_running = True
        if not session:
            session = ftrack_api.Session(auto_connect_event_hub=True)

        # Wait until session has connected event hub
        if session._auto_connect_event_hub_thread:
            # Use timeout from session (since ftrack-api 2.1.0)
            timeout = getattr(session, "request_timeout", 60)
            self.log.info("Waiting for event hub to connect")
            started = time.time()
            while not session.event_hub.connected:
                if (time.time() - started) > timeout:
                    raise RuntimeError((
                        "Connection to Ftrack was not created in {} seconds"
                    ).format(timeout))
                time.sleep(0.1)

        elif not session.event_hub.connected:
            self.log.info("Connecting event hub")
            session.event_hub.connect()

        self.session = session
        if load_files:
            if not self.handler_paths:
                self.log.warning((
                    "Paths to event handlers are not set."
                    " Ftrack server won't launch."
                ))
                self.is_running = False
                return

            self.set_files(self.handler_paths)

            msg = "Registration of event handlers has finished!"
            self.log.info(len(msg) * "*")
            self.log.info(msg)

        # keep event_hub on session running
        self.session.event_hub.wait()
        self.is_running = False


def get_handler_paths() -> list[str]:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    handler_paths = [
        os.path.join(current_dir, "default_handlers"),
    ]
    return handler_paths


def main():
    os.environ["OPENPYPE_SERVER_URL"] = os.environ["AY_SERVER_URL"]
    os.environ["OPENPYPE_TOKEN"] = os.environ["AY_API_KEY"]

    handler_paths = get_handler_paths()
    settings = get_project_settings()
    ftrack_settings = settings["ftrack"]
    service_settings = ftrack_settings["service_settings"]
    session = OPServerSession(
        ftrack_settings["ftrack_server"],
        service_settings["api_key"],
        service_settings["username"],
        auto_connect_event_hub=False
    )
    server = FtrackServer(handler_paths)
    server.run_server(session)
