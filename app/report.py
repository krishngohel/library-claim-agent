"""Human-readable report, built only from the packet dict (so it can never disagree with the JSON)."""

from html import escape


def _frame_link(ref: str) -> str:
    if not ref:
        return ""
    file = ref.split("@")[0]
    return f'<a href="frames/{escape(file)}" target="_blank">{escape(ref)}</a>'


def _price(p: dict, key: str = "amount") -> str:
    if not p or p.get(key) is None:
        return '<span class="none">none</span>'
    flag = " (converted)" if p.get("converted") else ""
    link = f'<a href="{escape(p["url"])}" target="_blank">{p[key]:.2f}</a>' if p.get("url") else f"{p[key]:.2f}"
    return f'{link}{flag}<br><small>{escape(p.get("source", ""))}<br>{escape(p.get("retrieved_at", ""))}</small>'


def render(packet: dict) -> str:
    s, room, t, ccy = packet["sweep"], packet["room"], packet["totals"], packet["sweep"]["currency"]
    book_rows = "".join(
        f"<tr class='{b['status']}'><td>{b['id']}</td><td>{escape(b['shelf'])} #{b['position']}</td>"
        f"<td>{b['status']}</td><td>{escape(b['title']) or '<i>unreadable</i>'}<br><small>{escape(b['author'])} "
        f"{escape(b['edition'])} {escape(b['isbn'])}</small></td><td>{b['id_confidence']}</td>"
        f"<td>{b['spine_height_cm'] or '-'} x {b['spine_thickness_cm'] or '-'}<br><small>{escape(b['scale_method'])}</small></td>"
        f"<td>{_price(b['replacement_cost'])}</td><td>{_price(b['used_value'])}</td><td>{_frame_link(b['frame_ref'])}</td></tr>"
        for b in packet["books"])
    item_rows = "".join(
        f"<tr class='{i['status']}'><td>{i['id']}</td><td>{escape(i['category'])}</td><td>{escape(i['description'])}"
        f"<br><small>{escape(i['brand_model'])}</small></td><td>{i['dimensions_cm']['w'] or '-'} x {i['dimensions_cm']['h'] or '-'}</td>"
        f"<td>{i['status']}</td><td>{_price(i['replacement_cost'], 'low')}</td><td>{_price(i['replacement_cost'], 'high')}</td>"
        f"<td>{_frame_link(i['frame_ref'])}</td></tr>"
        for i in packet["items"])
    review_rows = "".join(f"<tr><td>{escape(r['ref_id'])}</td><td>{escape(r['reason'])}</td></tr>"
                          for r in packet["review_queue"])
    m = packet["metrics"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Claim packet {escape(s['id'])}</title>
<style>body{{font:14px system-ui;margin:24px;max-width:1200px}}table{{border-collapse:collapse;width:100%;margin:8px 0 24px}}
td,th{{border:1px solid #ccc;padding:4px 6px;vertical-align:top;text-align:left}}small{{color:#555}}
.none{{color:#999}}.needs_appraisal{{background:#fff4d6}}.unidentified{{background:#eee}}</style></head><body>
<h1>Library contents claim - sweep {escape(s['id'])}</h1>
<p>Captured {escape(s['captured_at'])} on {escape(s['device'] or 'unknown device')}, {s['duration_s']} s,
{escape(s['country'])} / {escape(ccy)}. Frames: {escape(s['frames_dir'])}</p>
<h2>Totals ({escape(ccy)}) - computed from the lines below</h2>
<table><tr><th>Books</th><td>{t['book_count']} ({t['books_identified']} identified, {t['books_unidentified']} unidentified,
{t['books_needs_appraisal']} for appraisal)</td></tr>
<tr><th>Shelf run</th><td>{t['shelf_run_m']} m</td></tr>
<tr><th>Books replacement cost</th><td>{t['books_replacement_cost']:.2f}</td></tr>
<tr><th>Books used value</th><td>{t['books_used_value']:.2f}</td></tr>
<tr><th>Items replacement cost</th><td>{t['items_replacement_cost_low']:.2f} - {t['items_replacement_cost_high']:.2f}</td></tr>
<tr><th>Lines excluded from totals</th><td>{t['excluded_from_totals']} (unpriced or needs appraisal)</td></tr></table>
<h2>Room</h2>
<table><tr><th>Length x width x height</th><td>{room['length_m']} x {room['width_m']} x {room['height_m']} m</td></tr>
<tr><th>Floor area</th><td>{room['floor_area_m2']} m² / {room.get('floor_area_ft2', '')} ft²</td></tr>
<tr><th>Wall area (gross)</th><td>{room['wall_area_m2']} m² / {room.get('wall_area_ft2', '')} ft²</td></tr>
<tr><th>Wall area covered by shelving</th><td>{room['shelved_wall_area_m2']} m² / {room.get('shelved_wall_area_ft2', '')} ft²</td></tr>
<tr><th>Scale method</th><td>{escape(room['scale_method'])}</td></tr>
<tr><th>Confidence / notes</th><td>{room['confidence']} - {escape('; '.join(room.get('shape_notes', [])))}</td></tr></table>
<h2>Review queue ({len(packet['review_queue'])})</h2><table><tr><th>Line</th><th>Reason</th></tr>{review_rows}</table>
<h2>Books</h2><table><tr><th>ID</th><th>Shelf</th><th>Status</th><th>Title</th><th>ID conf.</th><th>H x T cm</th>
<th>Replacement</th><th>Used</th><th>Frame</th></tr>{book_rows}</table>
<h2>Other contents</h2><table><tr><th>ID</th><th>Category</th><th>Description</th><th>W x H cm</th><th>Status</th>
<th>Low</th><th>High</th><th>Frame</th></tr>{item_rows}</table>
<h2>Cost and latency</h2><pre>{escape(str(m))}</pre>
</body></html>"""
