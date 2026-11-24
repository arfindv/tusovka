from .validation import app_tz

MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
          "серпня", "вересня", "жовтня", "листопада", "грудня"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]


def register_filters(app):
    @app.template_filter("dt")
    def fmt_dt(dt):
        local = dt.astimezone(app_tz())
        return f"{WEEKDAYS[local.weekday()]}, {local.day} {MONTHS[local.month - 1]} {local.year}, {local:%H:%M}"

    @app.template_filter("day")
    def fmt_day(dt):
        return str(dt.astimezone(app_tz()).day)

    @app.template_filter("mon")
    def fmt_mon(dt):
        return MONTHS[dt.astimezone(app_tz()).month - 1][:3]

    @app.template_filter("plural_seats")
    def plural_seats(n):
        n = int(n)
        if n % 10 == 1 and n % 100 != 11:
            word = "місце"
        elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            word = "місця"
        else:
            word = "місць"
        return f"{n} {word}"

    @app.context_processor
    def _ctx():
        return {"tz_name": app.config["APP_TIMEZONE"]}
