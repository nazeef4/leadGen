"""Command line entry point.

    python -m leadgen serve                 # local web UI + API
    python -m leadgen serve --desktop       # same, inside a native window
    python -m leadgen demo                  # seed a fully worked example
    python -m leadgen suggest "HVAC repair" # niche/geography advisor
    python -m leadgen preview --offering .. # dry-run copy for a sample lead
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import get_settings


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .db import create_all, init_engine

    settings = get_settings()
    init_engine(settings)
    create_all()
    host, port = args.host or settings.host, args.port or settings.port

    if args.desktop:
        return _serve_desktop(host, port)

    print(f"\n  LeadGen Studio  →  http://{host}:{port}\n")
    uvicorn.run("leadgen.app:app", host=host, port=port, reload=args.reload, log_level="info")
    return 0


def _serve_desktop(host: str, port: int) -> int:  # pragma: no cover - needs a GUI
    import threading

    import uvicorn

    config = uvicorn.Config("leadgen.app:app", host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    try:
        import webview  # type: ignore
    except ImportError:
        print("pywebview is not installed — falling back to the browser. pip install 'leadgen[desktop]'")
        import webbrowser

        webbrowser.open(f"http://{host}:{port}")
        server.run()
        return 0
    import time

    time.sleep(1.5)
    webview.create_window("LeadGen Studio", f"http://{host}:{port}", width=1440, height=900)
    webview.start()
    server.should_exit = True
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .db import create_all, init_engine
    from .demo_data import seed

    init_engine(get_settings())
    create_all()
    summary = seed(leads_per_place=args.leads, with_messages=not args.no_messages)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    from .services.niche_advisor import get_niche_advisor

    advisor = get_niche_advisor()
    geo_filter = None
    if args.country:
        geo_filter = {"selections": [{"country": args.country, "state": "*", "city": "*"}]}
    result = advisor.suggest(args.offering, geo_filter, top_n=args.top, use_llm=not args.no_llm)
    print(f"\nArchetype: {result['archetypeLabel']} ({result['primaryArchetype']})")
    print(f"Candidates ranked: {result['candidateCount']}  source: {result['source']}\n")
    for item in result["suggestions"]:
        print(
            f"  {item['score']:>5.1f}  {item['fit']:<8} {item['label']:<42} "
            f"{item['avgSummerC']}°C  {'; '.join(item['reasons'][:2])}"
        )
    print(f"\nSearch terms : {', '.join(result['searchTerms'][:6])}")
    print(f"Target types : {', '.join(result['targetCategories'][:6])}")
    print(f"Seasonality  : {result['seasonality']}")
    print(f"\nStrategy: {result['strategy']}\n")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    from .services.compliance import get_compliance_engine
    from .services.copywriter import OfferConfig, get_copywriter
    from .services.niche_advisor import get_niche_advisor

    lead = {
        "id": 1,
        "business_name": args.business,
        "contact_name": args.contact or "",
        "email": "hello@example.com",
        "city": args.city,
        "state": "",
        "category": args.category,
        "rating": 4.7,
        "review_count": 92,
    }
    campaign = {
        "service_offering": args.offering,
        "niche": args.offering,
        "template_key": args.template,
        "sender_name": args.sender or "The team",
    }
    offers = OfferConfig(
        free_demo_call=args.demo_call,
        free_audit=args.free_audit,
        case_study=args.case_study,
        discount_percent=args.discount,
        limited_slots=args.slots,
        no_follow_up_pressure=args.no_pressure,
    )
    hooks = get_niche_advisor().suggest(args.offering, None, use_llm=False)["hooks"]
    copy = get_copywriter().generate(lead, campaign, offers, hooks, prefer_llm=not args.no_llm)
    report = get_compliance_engine().check_content(copy.subject, copy.body_text, copy.body_html)
    print(f"\nSubject: {copy.subject}\n\n{copy.body_text}\n")
    print(f"compliance score: {report.score}/100  blocked={report.blocked}")
    for issue in report.issues:
        print(f"  [{issue.severity}] {issue.code}: {issue.message}")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    from .db import create_all, init_engine
    from .services.geo import get_geo_service
    from .services.llm import get_llm
    from .services.scrapers.pipeline import SCRAPER_REGISTRY, get_pipeline

    settings = get_settings()
    init_engine(settings)
    create_all()
    geo = get_geo_service()
    print(f"app            : {settings.app_name} v{settings.version}")
    print(f"state dir      : {settings.state_dir}")
    print(f"database       : {settings.sqlalchemy_url}")
    print(f"geo dataset    : {len(geo.countries())} countries")
    print(f"llm            : {get_llm().info()}")
    pipeline = get_pipeline()
    for name in SCRAPER_REGISTRY:
        available = pipeline.build_scraper(name) is not None
        print(f"scraper {name:<14}: {'available' if available else 'unavailable (needs API key)'}")
    print(f"daily cap      : {settings.daily_recipient_cap} recipients")
    print(f"delay window   : {settings.min_delay_seconds}-{settings.max_delay_seconds}s")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="leadgen", description="LeadGen Studio")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the local web app")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--desktop", action="store_true", help="open in a native window")
    serve.set_defaults(func=cmd_serve)

    demo = sub.add_parser("demo", help="seed a fully worked example campaign")
    demo.add_argument("--leads", type=int, default=6, help="leads per target city")
    demo.add_argument("--no-messages", action="store_true")
    demo.set_defaults(func=cmd_demo)

    suggest = sub.add_parser("suggest", help="niche + geography recommendations")
    suggest.add_argument("offering")
    suggest.add_argument("--country", default=None)
    suggest.add_argument("--top", type=int, default=12)
    suggest.add_argument("--no-llm", action="store_true")
    suggest.set_defaults(func=cmd_suggest)

    preview = sub.add_parser("preview", help="render one email for a sample lead")
    preview.add_argument("--offering", required=True)
    preview.add_argument("--business", default="Desert Air Conditioning LLC")
    preview.add_argument("--contact", default="")
    preview.add_argument("--city", default="Phoenix")
    preview.add_argument("--category", default="HVAC contractor")
    preview.add_argument("--template", default="consultative")
    preview.add_argument("--sender", default=None)
    preview.add_argument("--demo-call", action="store_true")
    preview.add_argument("--free-audit", action="store_true")
    preview.add_argument("--case-study", action="store_true")
    preview.add_argument("--discount", type=int, default=0)
    preview.add_argument("--slots", type=int, default=0)
    preview.add_argument("--no-pressure", action="store_true")
    preview.add_argument("--no-llm", action="store_true")
    preview.set_defaults(func=cmd_preview)

    doctor = sub.add_parser("doctor", help="check configuration and integrations")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
