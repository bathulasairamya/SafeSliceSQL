"""Actionable explanations without exposing database statements or credentials."""

import re
import tkinter as tk
from tkinter import ttk


def explain_error(message):
    code = re.search(r"code (\d+)", message)
    number = int(code[1]) if code else None
    if number in {2002, 2003, 2005, 2006, 2013}:
        return (
            "Cannot reach MySQL",
            "Check that MySQL is running and the server address and port are correct, then retry.",
        )
    if number == 1045:
        return (
            "MySQL rejected the login",
            "Check the username and password in Connection settings, then retry.",
        )
    if number in {1044, 1142, 1227}:
        return (
            "Permission is missing",
            "This MySQL account cannot perform the requested operation. Use an account with the required access in Connection settings.",
        )
    if number in {1049, 1146} or "no longer available" in message.lower():
        return (
            "Saved data is unavailable",
            "The database or table may have been removed. Check Connection settings. Saved reports and charts can still be viewed offline.",
        )
    if "limit" in message.lower() or "timeout" in message.lower():
        return (
            "This action reached a limit",
            "Try a more specific search or a smaller input. Open Options to review the configured limits before retrying.",
        )
    if "no such file" in message.lower() or "does not exist" in message.lower():
        return (
            "Input file is unavailable",
            "Select the file again from its current location, then retry.",
        )
    return (
        "This action could not finish",
        "Review the details below, correct the input or settings, and retry. Earlier saved results are still available.",
    )


def show_error(root, message, *, retry=None, settings=None):
    title, advice = explain_error(message)
    dialog = tk.Toplevel(root)
    dialog.title(title)
    ttk.Label(dialog, text=title, font=("Segoe UI", 14, "bold"), padding=14).pack(anchor="w")
    ttk.Label(dialog, text=advice, wraplength=560, padding=14).pack(fill="x")
    ttk.Label(dialog, text="Details: " + message, wraplength=560, padding=14).pack(fill="x")
    actions = ttk.Frame(dialog, padding=12)
    actions.pack(fill="x")

    def act(callback):
        dialog.destroy()
        callback()

    if retry:
        ttk.Button(actions, text="Retry", command=lambda: act(retry)).pack(side="left", padx=4)
    if settings:
        ttk.Button(actions, text="Connection settings", command=lambda: act(settings)).pack(
            side="left", padx=4
        )
    ttk.Button(actions, text="Close", command=dialog.destroy).pack(side="right")
