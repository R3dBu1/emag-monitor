import os

CONFIG = {
    "interval_ore": 2,
    "pauza_secunde": 7,
    "email": {
        "from": os.environ.get("EMAIL_FROM", "emailul_tau@gmail.com"),
        "to": os.environ.get("EMAIL_TO", "emailul_tau@gmail.com"),
        "username": os.environ.get("EMAIL_FROM", "emailul_tau@gmail.com"),
        "password": os.environ.get("EMAIL_PASS", "app_password_aici"),
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 465,
    },
}
