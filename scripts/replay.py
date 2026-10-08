"""Re-run the post-sweep pipeline on a saved sweep, without the phone or the live agent.

    python -m scripts.replay <sweep_id>                 # same country as the sweep
    python -m scripts.replay <sweep_id> --fresh         # also redo shelf consolidation

Used for tuning and for the failure log: change one stage, replay, compare against ground truth.
"""

import argparse
import asyncio

from app import pipeline
from app.sweep import Sweep


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sweep_id")
    parser.add_argument("--fresh", action="store_true", help="discard consolidated shelves and redo them")
    args = parser.parse_args()

    sweep = Sweep.load_state(args.sweep_id)
    if args.fresh:
        sweep.shelf_books = {}
    sweep.stage_seconds = {}
    packet = asyncio.run(pipeline.finish_sweep(sweep))
    print(f"{packet['totals']['book_count']} books, review queue {len(packet['review_queue'])}, "
          f"stages {packet['metrics']['stage_seconds']}")
    print(f"wrote sweeps/{sweep.id}/claim_packet.json and report.html")


if __name__ == "__main__":
    main()
