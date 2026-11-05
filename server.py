"""Thin launcher kept for backwards compatibility: the server lives in plugins/ssh-connector/server.py."""
import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins", "ssh-connector", "server.py"),
               run_name="__main__")
