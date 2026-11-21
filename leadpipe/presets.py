"""Service types: one setting that adapts the whole pipeline to what you sell.

Each type sets (1) which problems make a business a good lead (signal weights), (2) how the fix is worded in
messages, and (3) the free offer for businesses without a website. Choose it in Settings → Setup, or set
`profile.service_type` in config.yaml. "custom" keeps your own `signals:` weights.
"""
from __future__ import annotations

ZERO = {k: 0 for k in (
    "no_website", "site_broken", "instagram_only", "no_online_booking", "not_mobile_friendly", "slow_site", "no_https",
    "outdated_site", "running_ads", "diy_builder", "hiring", "no_contact_form", "weak_seo", "no_analytics", "low_rating",
    "few_reviews", "blog_stale", "no_schema_markup", "agency_built", "no_live_chat", "startup_backed", "recently_funded")}

PRESETS: dict[str, dict] = {
    "website": {
        "label": "Websites / web design",
        "weights": {"no_website": 25, "site_broken": 20, "instagram_only": 20, "no_online_booking": 20,
                    "not_mobile_friendly": 15, "slow_site": 10, "no_https": 10, "outdated_site": 10, "running_ads": 10,
                    "diy_builder": 5, "hiring": 5, "no_contact_form": 5, "weak_seo": 5, "no_analytics": 5,
                    "low_rating": 5, "few_reviews": 5},
        "quick_win_no_site": "a free mock-up of a simple website for you",
        "fixes": {},
        "costs": {},
    },
    "automation": {
        "label": "AI automation / chatbots / WhatsApp assistants",
        # Businesses that run on phone calls and DMs (no website / Instagram only) are prime targets for a WhatsApp assistant.
        "weights": {"no_online_booking": 25, "no_live_chat": 25, "no_website": 25, "instagram_only": 20,
                    "no_contact_form": 15, "hiring": 15, "running_ads": 15, "few_reviews": 10, "low_rating": 10,
                    "not_mobile_friendly": 5},
        "quick_win_no_site": "a free 2-minute video showing how your WhatsApp enquiries could be answered automatically",
        "fixes": {
            "no_online_booking": "an assistant that books appointments automatically, day and night",
            "no_live_chat": "a WhatsApp assistant that answers common questions instantly",
            "no_contact_form": "an enquiry form that replies and follows up automatically",
            "hiring": "automating the repetitive enquiry and booking work",
            "running_ads": "instant automatic replies and follow-ups for every ad enquiry",
            "few_reviews": "an automatic review request after every visit",
            "low_rating": "automatic follow-ups that catch unhappy customers before they post",
            "no_website": "a WhatsApp assistant that answers enquiries from your Google listing",
            "instagram_only": "an assistant that answers Instagram and WhatsApp enquiries instantly",
            "not_mobile_friendly": "a WhatsApp booking flow so people don't need the website at all",
        },
        "costs": {
            "no_website": "When people message or call after hours, most go to whoever answers first.",
            "instagram_only": "Enquiries that come in by DM late at night often wait until morning, and by then they've booked elsewhere.",
        },
    },
    "marketing": {
        "label": "Ads / digital marketing",
        "weights": {"running_ads": 25, "no_analytics": 20, "no_contact_form": 10, "not_mobile_friendly": 10,
                    "slow_site": 10, "weak_seo": 10, "few_reviews": 5, "low_rating": 5, "instagram_only": 10, "hiring": 5},
        "quick_win_no_site": "a free 2-minute plan for getting more enquiries from Google and Instagram",
        "fixes": {"no_analytics": "tracking that shows which ads and pages bring enquiries",
                  "running_ads": "a landing page and tracking built around one clear action"},
        "costs": {},
    },
    "seo": {
        "label": "SEO / Google ranking",
        "weights": {"weak_seo": 25, "no_schema_markup": 20, "blog_stale": 15, "slow_site": 15, "not_mobile_friendly": 15,
                    "no_analytics": 10, "few_reviews": 10, "no_website": 10, "outdated_site": 5},
        "quick_win_no_site": "a free 2-minute plan for showing up on Google Maps and search",
        "fixes": {},
        "costs": {},
    },
    "reviews": {
        "label": "Reviews / reputation",
        "weights": {"low_rating": 30, "few_reviews": 30, "no_online_booking": 5, "no_live_chat": 5, "running_ads": 5},
        "quick_win_no_site": "a free 2-minute plan to get more 5-star Google reviews",
        "fixes": {"few_reviews": "an automatic review request after every visit",
                  "low_rating": "a simple system that asks happy customers for reviews and catches unhappy ones privately"},
        "costs": {},
    },
}


def preset(cfg) -> dict | None:
    return PRESETS.get(str(cfg.get("profile.service_type") or "").lower())


def weights_for(name: str) -> dict:
    p = PRESETS.get(name)
    return {**ZERO, **p["weights"]} if p else {}


def override(cfg, key: str, field: str) -> str | None:
    """Service-specific wording for a signal's fix/cost, if the chosen type has one."""
    p = preset(cfg)
    if not p:
        return None
    return (p.get("fixes") if field == "fix" else p.get("costs") if field == "cost" else {}).get(key)
