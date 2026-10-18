"""The bundled synthetic sample is an executable acceptance fixture."""
from pathlib import Path
from poi_audit.core import load_config, load_boundary, read_rows, audit_rows


def test_shipped_demo_expected_counts():
    config = Path(__file__).resolve().parents[1] / 'examples' / 'demo.json'
    c, base = load_config(config)
    rows, _ = read_rows(c, base)
    counts = audit_rows(rows, c, load_boundary(c, base))
    assert counts == {'raw': 140, 'valid': 136, 'unique': 132, 'kept': 130,
                      'rejected': 4, 'exact_duplicate': 1, 'id_duplicate': 1,
                      'id_conflict': 2, 'outside_boundary': 2,
                      'candidate_rows': 2, 'candidate_groups': 1}
    assert c['synthetic'] is True
    assert next(r for r in rows if r['source_id'] == 'COLOC')['status'] == 'kept'
    assert next(r for r in rows if r['source_id'] == 'UNKNOWN')['category'] == 'Unmapped'
    assert next(r for r in rows if r['source_id'] == 'EDGE')['status'] == 'kept'
    assert next(r for r in rows if r['source_id'] == 'HOLE')['status'] == 'outside_boundary'
