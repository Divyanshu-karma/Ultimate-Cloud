BASE_APPLICATION = {
    "application_id":     "123456789",
    "mark_text":          "ADAMS APPLE",
    "mark_type":          "standard_character",
    "goods_services": [
        {"class": "029", "description": "Dried fruits; Dried vegetables"}
    ],
    "application_status": "initial_examination",
    "event_trigger":      "first_review",
}

def make_app(**overrides) -> dict:
    app = BASE_APPLICATION.copy()
    app.update(overrides)
    return app