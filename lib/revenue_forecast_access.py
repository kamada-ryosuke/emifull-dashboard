"""売上収支予測表の施設ログイン定義。"""

FACILITY_FORECAST_PASSWORD_HASH = (
    "pbkdf2_sha256$200000$emifull_forecast_20260718$"
    "7a53814b4a72c8526839578226a0a73021cac00d5456a7698294517bab846eea"
)

FACILITY_FORECAST_USERS = [
    {"email": "sorato@sorato-umie.com", "facility_label": "SORATOいなみ"},
    {"email": "umie@sorato-umie.com", "facility_label": "UMIEいなみ"},
    {"email": "umie-2@sorato-umie.com", "facility_label": "UMIEいなみ第二教室"},
    {"email": "sorato-2@sorato-umie.com", "facility_label": "SORATOいなみ第二教室"},
    {"email": "bloom-inami@sorato-umie.com", "facility_label": "BLOOMいなみ"},
    {"email": "sorato-tenri@sorato-umie.com", "facility_label": "SORATOてんり"},
    {"email": "umie-tenri@sorato-umie.com", "facility_label": "UMIEてんり"},
    {"email": "bloom-tenri@sorato-umie.com", "facility_label": "BLOOMてんり"},
    {"email": "tenri-sh@emifull-group.or.jp", "facility_label": "Hinodeシェアホーム天理"},
    {"email": "kids-kakogawa@emifull-group.or.jp", "facility_label": "カラダキッズかこがわ"},
    {"email": "kakogawa-sh@emifull-group.or.jp", "facility_label": "Hinodeシェアホーム加古川"},
    {"email": "college-kakogawa@emifull-group.or.jp", "facility_label": "ジョブカレッジかこがわ"},
    {"email": "nojigiku-kakogawa@emifull-group.or.jp", "facility_label": "のじぎく加古川"},
    {"email": "nojigiku-takasago@emifull-group.or.jp", "facility_label": "のじぎく高砂"},
    {"email": "nojigiku-inami@emifull-group.or.jp", "facility_label": "のじぎく稲美"},
    {"email": "kids-tenri@emifull-group.or.jp", "facility_label": "カラダキッズてんり"},
]


def facility_forecast_profile(email):
    key = (email or "").strip().lower()
    for user in FACILITY_FORECAST_USERS:
        if user["email"] == key:
            return user
    return None
