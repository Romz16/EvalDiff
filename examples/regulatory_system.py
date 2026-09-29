def summarize(payload):
    notice = payload.get("text", "")
    return {
        "summary": f"The organization must submit a corrected filing within 30 days. {notice}".strip(),
        "deadline_days": 30,
        "action": "submit corrected filing",
    }

