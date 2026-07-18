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

FORECAST_MANAGER_USERS = [
    {
        "email": "fukaya.kkr@emifull-group.or.jp",
        "name": "天理エリア担当",
        "facility_labels": [
            "SORATOてんり",
            "UMIEてんり",
            "BLOOMてんり",
            "カラダキッズてんり",
        ],
    },
    {
        "email": "kanbe.tkhr@emifull-group.or.jp",
        "name": "稲美・加古川エリア担当",
        "facility_labels": [
            "SORATOいなみ",
            "UMIEいなみ",
            "UMIEいなみ第二教室",
            "SORATOいなみ第二教室",
            "BLOOMいなみ",
            "カラダキッズかこがわ",
            "Hinodeシェアホーム加古川",
            "ジョブカレッジかこがわ",
        ],
    },
    {
        "email": "kuroda.yusk@emifull-group.or.jp",
        "name": "のじぎく高砂担当",
        "facility_labels": ["のじぎく高砂"],
    },
    {
        "email": "nishitsuji.msys@emifull-group.or.jp",
        "name": "Hinodeシェアホーム天理担当",
        "facility_labels": ["Hinodeシェアホーム天理"],
    },
    {
        "email": "oketani.msm@emifull-group.or.jp",
        "name": "のじぎく担当",
        "facility_labels": [
            "のじぎく稲美",
            "のじぎく加古川",
        ],
    },
    {
        "email": "morita.yshr@emifull-group.or.jp",
        "name": "全施設担当",
        "facility_labels": [],
        "all_facilities": True,
    },
]

FORECAST_LOGIN_USERS = [
    {
        "email": user["email"],
        "name": user["facility_label"],
        "position": "施設管理者",
        "reset_existing": True,
    }
    for user in FACILITY_FORECAST_USERS
] + [
    {
        "email": user["email"],
        "name": user["name"],
        "position": "担当管理者",
        "reset_existing": False,
    }
    for user in FORECAST_MANAGER_USERS
]


def facility_forecast_profile(email):
    key = (email or "").strip().lower()
    for user in FACILITY_FORECAST_USERS:
        if user["email"] == key:
            return {
                **user,
                "facility_labels": [user["facility_label"]],
                "display_label": user["facility_label"],
                "dedicated": True,
                "all_facilities": False,
            }
    for user in FORECAST_MANAGER_USERS:
        if user["email"] == key:
            labels = list(user.get("facility_labels") or [])
            display_label = "全施設" if user.get("all_facilities") else "、".join(labels)
            return {
                **user,
                "facility_label": display_label,
                "facility_labels": labels,
                "display_label": display_label,
                "dedicated": False,
                "all_facilities": bool(user.get("all_facilities")),
            }
    return None
