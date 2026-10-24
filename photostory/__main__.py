"""Command line:  python -m photostory            (opens the app in your browser)
                python -m photostory scan ...   (find trips without the browser)
                python -m photostory pdf        (make the PDF from the last scan)"""
import argparse
import os
from pathlib import Path

from . import app, pipeline, report

DEFAULT_OUT = str(Path.home() / "PhotoStory")


def main():
    ap = argparse.ArgumentParser(prog="photostory", description="Make a PDF of trips taken together from photos.")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"where project files and PDFs go (default {DEFAULT_OUT})")
    sub = ap.add_subparsers(dest="cmd")
    srv = sub.add_parser("app", help="open the app in your browser (the default)")
    srv.add_argument("--port", type=int, default=8765)
    sc = sub.add_parser("scan", help="find trips and suggest photos")
    sc.add_argument("--photos", required=True, help="folder of photos, e.g. your Dropbox Camera Uploads")
    sc.add_argument("--people", default="", help="folder with one subfolder of reference photos per person")
    sc.add_argument("--from", dest="date_from", default="", help="first date, YYYY-MM-DD")
    sc.add_argument("--to", dest="date_to", default="", help="last date, YYYY-MM-DD")
    sc.add_argument("--home", action="append", default=[], help="home town or 'lat,lon'; repeat for several")
    sc.add_argument("--min-km", type=float, default=50)
    sc.add_argument("--min-nights", type=int, default=1, help="0 includes day trips")
    sc.add_argument("--photos-per-trip", type=int, default=4)
    pd = sub.add_parser("pdf", help="make the PDF from the last scan")
    pd.add_argument("--output", default="", help="PDF file to write")
    args = ap.parse_args()

    if args.cmd in (None, "app"):
        app.serve(args.out, port=getattr(args, "port", 8765))
    elif args.cmd == "scan":
        settings = {"photos": args.photos, "people_dir": args.people, "date_from": args.date_from,
                    "date_to": args.date_to, "home": "\n".join(args.home), "min_km": args.min_km,
                    "min_nights": args.min_nights, "photos_per_trip": args.photos_per_trip}
        last = [None]

        def show(pct, msg):
            if msg != last[0]:
                print(f"[{pct:3d}%] {msg}")
                last[0] = msg

        proj = pipeline.run(settings, args.out, show)
        for n in proj["notes"]:
            print("Note:", n)
        for i, t in enumerate(proj["trips"], 1):
            print(f"{i:2d}. {t['start'][:10]} to {t['end'][:10]}  {t['nights']:2d} nights  {t['title']}")
    elif args.cmd == "pdf":
        proj = pipeline.load_project(args.out)
        if not proj:
            raise SystemExit("No scan found. Run the scan first.")
        out = args.output or os.path.join(args.out, "Travel together.pdf")
        report.build_pdf(proj, out)
        print("Wrote", out)


if __name__ == "__main__":
    main()
