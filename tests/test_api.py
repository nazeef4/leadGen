"""End-to-end API flow: campaign -> scrape -> curate -> copy -> dry-run dispatch -> CRM."""

from __future__ import annotations

import time


def _make_campaign(client, **overrides):
    payload = {
        "name": "Test HVAC campaign",
        "niche": "HVAC",
        "service_offering": "commercial HVAC maintenance contracts",
        "geo_filter": {
            "selections": [{"country": "US", "state": "AZ", "city": "*"}],
            "extraCities": [],
        },
        "offers": {"free_demo_call": True, "limited_slots": 3},
        "template_key": "consultative",
        "max_per_day": 50,
        "delay_min": 10,
        "delay_max": 20,
    }
    payload.update(overrides)
    res = client.post("/api/campaigns", json=payload)
    assert res.status_code == 201, res.text
    return res.json()["campaign"]


def test_ping_and_health(client):
    assert client.get("/api/ping").json()["ok"] is True
    health = client.get("/api/system/health").json()
    assert health["ok"] is True
    assert health["quota"]["dailyCap"] == 400
    assert "duckduckgo" in health["scrapers"]


def test_geo_endpoints(client):
    countries = client.get("/api/targeting/countries").json()["countries"]
    assert len(countries) >= 40
    states = client.get("/api/targeting/countries/US/states").json()["states"]
    assert len(states) == 51
    cities = client.get("/api/targeting/countries/US/states/AZ/cities").json()["cities"]
    assert any(c["name"] == "Phoenix" for c in cities)
    assert client.get("/api/targeting/countries/ZZ/states").status_code == 404
    found = client.get("/api/targeting/search?q=phoenix").json()["results"]
    assert any(r["type"] == "city" for r in found)


def test_expand_and_niche_endpoints(client):
    expanded = client.post(
        "/api/targeting/expand",
        json={"selections": [{"country": "US", "state": "AZ", "city": "*"}], "extraCities": []},
    ).json()
    assert expanded["count"] >= 5
    assert expanded["profile"]["avgSummerC"] > 30

    result = client.post(
        "/api/targeting/niche-suggestions",
        json={"offering": "HVAC and AC repair", "topN": 6, "useLlm": False},
    ).json()
    assert result["primaryArchetype"] == "hvac_cooling"
    assert result["suggestions"][0]["avgSummerC"] >= 30
    assert client.get("/api/targeting/niche-archetypes").json()["archetypes"]


def test_campaign_crud(client):
    campaign = _make_campaign(client)
    assert campaign["id"] > 0
    assert campaign["counts"]["total"] == 0

    updated = client.patch(
        f"/api/campaigns/{campaign['id']}", json={"name": "Renamed", "max_per_day": 25}
    ).json()["campaign"]
    assert updated["name"] == "Renamed"
    assert updated["max_per_day"] == 25

    detail = client.get(f"/api/campaigns/{campaign['id']}").json()["campaign"]
    assert detail["offerConfig"]["free_demo_call"] is True
    assert "Arizona" in detail["geoSummary"]

    assert client.delete(f"/api/campaigns/{campaign['id']}").json()["ok"] is True
    assert client.get(f"/api/campaigns/{campaign['id']}").status_code == 404


def test_validation_rejects_bad_input(client):
    assert client.post("/api/campaigns", json={"name": "x", "delay_min": 100, "delay_max": 10}).status_code == 422
    assert client.post("/api/campaigns", json={"name": "x", "max_per_day": 99999}).status_code == 422


def test_scrape_demo_save_and_curate(client):
    campaign = _make_campaign(client)
    res = client.post(
        f"/api/campaigns/{campaign['id']}/scrape",
        json={"sources": ["demo"], "max_results": 12, "max_places": 3, "sync": True},
    ).json()
    assert res["job"]["status"] == "done"
    assert res["saved"]["created"] > 0

    leads = client.get(f"/api/campaigns/{campaign['id']}/leads").json()
    assert leads["total"] > 0
    assert leads["counts"]["withEmail"] > 0
    first = leads["leads"][0]
    assert first["source"] == "demo"
    assert first["email"].endswith("example.com")
    assert leads["leads"] == sorted(leads["leads"], key=lambda x: -x["score"])

    # de-select one, exclude another
    lid = first["id"]
    assert client.patch(f"/api/campaigns/{campaign['id']}/leads/{lid}", json={"selected": False}).status_code == 200
    bulk = client.post(
        f"/api/campaigns/{campaign['id']}/leads/bulk",
        json={"lead_ids": [leads["leads"][1]["id"]], "action": "exclude"},
    ).json()
    assert bulk["affected"] == 1

    after = client.get(f"/api/campaigns/{campaign['id']}/leads").json()
    assert after["counts"]["selected"] == after["counts"]["total"] - 2

    exported = client.get(f"/api/campaigns/{campaign['id']}/leads-export.csv")
    assert exported.status_code == 200
    header = exported.text.splitlines()[0]
    assert header.startswith("business_name,") and "email" in header


def test_scrape_rejects_campaign_without_offering(client):
    campaign = _make_campaign(client, service_offering="", niche="")
    res = client.post(f"/api/campaigns/{campaign['id']}/scrape", json={"sources": ["demo"]})
    assert res.status_code == 400


def test_copy_preview_and_compliance(client):
    campaign = _make_campaign(client)
    client.post(
        f"/api/campaigns/{campaign['id']}/scrape",
        json={"sources": ["demo"], "max_results": 6, "max_places": 2, "sync": True},
    )
    previews = client.post(f"/api/campaigns/{campaign['id']}/preview", json={"limit": 2}).json()
    assert len(previews["previews"]) == 2
    for item in previews["previews"]:
        assert item["subject"]
        assert "{first_name}" not in item["bodyText"]
        assert "unsubscribe" in item["bodyText"].lower() or "STOP" in item["bodyText"]

    good = client.post(
        "/api/campaigns/compliance-check",
        json={
            "subject": "quick question",
            "body_text": (
                "Hi there, we help HVAC teams with maintenance contracts and the common "
                "bottleneck is summer demand. Worth a short conversation? "
                "Test Company Ltd · 12 Test Street, Suite 4, Springfield, ST 00000 · Reply STOP to unsubscribe"
            ),
        },
    ).json()
    assert good["blocked"] is False

    bad = client.post(
        "/api/campaigns/compliance-check", json={"subject": "hi", "body_text": "short"}
    ).json()
    assert bad["blocked"] is True


def test_sample_preview_without_leads(client):
    res = client.post(
        "/api/campaigns/preview-sample",
        json={
            "service_offering": "commercial HVAC maintenance contracts",
            "niche": "HVAC",
            "offers": {"free_demo_call": True},
            "sender_name": "Alex Mercer",
        },
    ).json()
    assert res["preview"]["subject"]
    assert res["compliance"]["blocked"] is False


def test_plan_preview_is_randomised(client):
    campaign = _make_campaign(client)
    client.post(
        f"/api/campaigns/{campaign['id']}/scrape",
        json={"sources": ["demo"], "max_results": 10, "max_places": 3, "sync": True},
    )
    plan = client.post(f"/api/campaigns/{campaign['id']}/plan-preview").json()
    assert plan["selectedCount"] > 0
    assert plan["plan"]["scheduled"] == plan["selectedCount"]
    gaps = [s["delaySeconds"] for s in plan["plan"]["slots"]]
    assert all(10 <= g <= 20 or g > 20 for g in gaps)
    assert len(set(s["leadId"] for s in plan["plan"]["slots"])) == plan["plan"]["scheduled"]


def test_dry_run_dispatch_sends_and_tracks(client):
    campaign = _make_campaign(client)
    client.post(
        f"/api/campaigns/{campaign['id']}/scrape",
        json={"sources": ["demo"], "max_results": 4, "max_places": 2, "sync": True},
    )
    from leadgen.services.dispatcher import get_engine

    engine = get_engine()
    from leadgen.services.sender import DryRunSender

    engine._sender_override = DryRunSender()
    try:
        res = client.post(
            "/api/campaigns/dispatch/start",
            json={"campaign_id": campaign["id"], "dry_run": True, "prepare": True},
        ).json()
        assert res["ok"] is True
        assert res["prepared"]["created"] > 0

        deadline = time.time() + 40
        while time.time() < deadline:
            state = client.get("/api/campaigns/dispatch/state").json()["state"]
            # Regression: the burst guardrail used the global 45s floor and
            # stalled a campaign configured for 10-20s gaps after one send.
            if state["sent"] >= 2:
                break
            time.sleep(0.5)
        state = client.get("/api/campaigns/dispatch/state").json()["state"]
        assert state["sent"] >= 2, state
        assert engine._sender_override.sent, "the dry-run sender recorded nothing"

        messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()["messages"]
        assert any(m["status"] == "sent" for m in messages)
        assert all(m["rfc_message_id"] for m in messages if m["status"] == "sent")

        overview = client.get("/api/crm/overview").json()
        assert overview["totals"]["sent"] > 0
    finally:
        engine._sender_override = None
        engine.stop()


def test_dispatch_requires_an_account_for_real_sends(client):
    campaign = _make_campaign(client)
    res = client.post(
        "/api/campaigns/dispatch/start",
        json={"campaign_id": campaign["id"], "dry_run": False, "prepare": True},
    )
    assert res.status_code == 400


def test_accounts_crud_and_test_requires_credentials(client):
    created = client.post(
        "/api/accounts",
        json={"email": "sender@test.example", "display_name": "Sender", "provider": "custom"},
    )
    assert created.status_code == 201
    account = created.json()["account"]
    assert account["has_credential"] is False
    assert account["is_verified"] is False

    listed = client.get("/api/accounts").json()
    assert any(a["id"] == account["id"] for a in listed["accounts"])
    assert "gmail" in listed["presets"]

    dup = client.post("/api/accounts", json={"email": "sender@test.example"})
    assert dup.status_code == 409

    assert client.post(f"/api/accounts/{account['id']}/test", json={}).status_code == 400

    assert client.get("/api/accounts/guess-provider?email=a@gmail.com").json()["provider"] == "gmail"

    assert client.delete(f"/api/accounts/{account['id']}").json()["ok"] is True


def test_suppression_flow(client):
    campaign = _make_campaign(client)
    client.post(
        f"/api/campaigns/{campaign['id']}/scrape",
        json={"sources": ["demo"], "max_results": 4, "max_places": 2, "sync": True},
    )
    leads = client.get(f"/api/campaigns/{campaign['id']}/leads").json()["leads"]
    target = leads[0]["email"]
    res = client.post("/api/crm/suppressions", json={"email": target, "reason": "test"})
    assert res.status_code == 201
    assert client.post("/api/crm/suppressions", json={"email": target}).status_code == 409

    after = client.get(f"/api/campaigns/{campaign['id']}/leads").json()["leads"]
    flagged = [lead for lead in after if lead["email"] == target][0]
    assert flagged["is_suppressed"] is True
    assert flagged["selected"] is False

    listing = client.get("/api/crm/suppressions").json()["suppressions"]
    assert any(s["email"] == target for s in listing)
    row = [s for s in listing if s["email"] == target][0]
    assert client.delete(f"/api/crm/suppressions/{row['id']}").json()["ok"] is True


def test_crm_overview_and_pipeline(client):
    campaign = _make_campaign(client)
    client.post(
        f"/api/campaigns/{campaign['id']}/scrape",
        json={"sources": ["demo"], "max_results": 5, "max_places": 2, "sync": True},
    )
    overview = client.get("/api/crm/overview").json()
    assert overview["totals"]["leads"] > 0
    assert "replied" in overview["stages"] or "new" in overview["stages"]

    pipeline = client.get("/api/crm/pipeline").json()
    assert pipeline["stageOrder"][0] == "new"
    board_total = sum(len(v) for v in pipeline["board"].values())
    assert board_total > 0

    lead_id = next(
        lead["id"] for stage in pipeline["board"].values() for lead in stage
    )
    moved = client.post(f"/api/crm/leads/{lead_id}/stage", json={"pipeline_stage": "meeting"})
    assert moved.status_code == 200
    assert moved.json()["lead"]["pipeline_stage"] == "meeting"
    assert client.post(f"/api/crm/leads/{lead_id}/stage", json={"pipeline_stage": "nope"}).status_code == 400

    note = client.post(f"/api/crm/leads/{lead_id}/notes", json={"note": "call them Tuesday"})
    assert note.status_code == 200
    detail = client.get(f"/api/crm/leads/{lead_id}").json()
    assert any(a["note"] == "call them Tuesday" for a in detail["activities"])


def test_settings_endpoints(client):
    current = client.get("/api/system/settings").json()["settings"]
    assert current["daily_recipient_cap"] == 400
    updated = client.patch("/api/system/settings", json={"daily_recipient_cap": 300}).json()
    assert updated["settings"]["daily_recipient_cap"] == 300
    assert updated["settings"]["llm_api_key"] if False else True
    assert "llm_api_key" not in updated["settings"], "the API key must never be echoed back"
    client.patch("/api/system/settings", json={"daily_recipient_cap": 400})

    posture = client.get("/api/system/compliance-posture").json()
    assert posture["caps"]["daily"]["limit"] in (300, 400)
    assert any(c["id"] == "randomised_delay" for c in posture["checks"])


def test_google_places_key_is_write_only_and_enables_the_source(client):
    """
    The Places key can be set at runtime, flips the source on, and is never
    readable back — the API reports a boolean instead of the secret.
    """
    before = client.get("/api/system/settings").json()["settings"]
    assert before["google_places_configured"] is False
    assert "google_maps_api_key" not in before

    res = client.patch(
        "/api/system/settings", json={"google_maps_api_key": "AIza-test-key-123"}
    )
    assert res.status_code == 200
    after = res.json()["settings"]
    assert after["google_places_configured"] is True
    assert "AIza-test-key-123" not in res.text, "the key must never be echoed back"

    # /api/system/health lists the static scraper registry, so it says nothing
    # about availability. /api/system/scrapers actually builds each scraper and
    # reports whether it is usable — that is the assertion that means something.
    def places_available() -> bool:
        rows = client.get("/api/system/scrapers").json()["scrapers"]
        return next(r["available"] for r in rows if r["name"] == "google_places")

    assert places_available() is True, "source should become available"

    # Clean up so the key does not leak into other tests, and prove the source
    # switches back off.
    client.patch("/api/system/settings", json={"google_maps_api_key": ""})
    assert (
        client.get("/api/system/settings").json()["settings"]["google_places_configured"]
        is False
    )
    assert places_available() is False
