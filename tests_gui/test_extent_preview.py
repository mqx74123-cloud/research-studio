"""Strict coordinate and provenance coverage for the opt-in point preview."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gui.coordinates import auto_utm, point_extent_preview
from gui.table_import import InputError


class PointExtentPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'selected.csv'
        self.write([(117.2, 31.8), (117.4, 32.0)])

    def tearDown(self):
        self.temp.cleanup()

    def write(self, pairs):
        with self.path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['经度', '纬度', 'number'])
            writer.writerows([[x, y, index + 1000] for index, (x, y) in enumerate(pairs)])

    def build(self, **kwargs):
        args = dict(convention='WGS84', crs='EPSG:4326', confirmed=True)
        args.update(kwargs)
        return point_extent_preview(self.path, '经度', '纬度', **args)

    def test_extent_is_buffered_rectangle_with_exact_provenance(self):
        before = self.path.read_bytes()
        feature, provenance = self.build(padding_percent=5)
        self.assertEqual(feature['type'], 'Feature')
        self.assertEqual(feature['geometry']['type'], 'Polygon')
        ring = feature['geometry']['coordinates'][0]
        self.assertEqual(len(ring), 5)
        self.assertEqual(ring[0], ring[-1])
        for value, expected in zip(provenance['preview_bounds_wgs84'], [117.19, 31.79, 117.41, 32.01]):
            self.assertAlmostEqual(value, expected)
        self.assertEqual(provenance['source_bounds_wgs84'], [117.2, 31.8, 117.4, 32.0])
        self.assertEqual(provenance['source_fields'], {'x': '经度', 'y': '纬度'})
        self.assertEqual(provenance['source_row_count'], 2)
        self.assertEqual(provenance['invalid_rows_skipped'], 0)
        self.assertFalse(provenance['uses_uploaded_boundary'])
        self.assertFalse(provenance['is_administrative_boundary'])
        self.assertFalse(provenance['is_formal_study_boundary'])
        self.assertIn('每侧', provenance['note'])
        self.assertIn('不是等距离米制缓冲', provenance['note'])
        self.assertIn('未裁剪省界', provenance['note'])
        self.assertEqual(feature['properties'], provenance)
        self.assertEqual(before, self.path.read_bytes(), 'The original table must not change')
        json.dumps(feature, allow_nan=False)

    def test_no_silent_inference_or_confirmation_coercion(self):
        for update in [{'confirmed': False}, {'confirmed': 'true'}, {'confirmed': 1},
                       {'convention': ''}, {'convention': 'GCJ-02'},
                       {'convention': 'CRS_NATIVE'}, {'crs': 'EPSG:3857'}, {'crs': None}]:
            with self.subTest(update=update), self.assertRaises(InputError):
                self.build(**update)

    def test_no_silent_column_switch_or_duplicate_fields(self):
        for x, y in [('number', '纬度'), ('missing', '纬度'), ('经度', '经度'), ('', '纬度')]:
            with self.subTest(x=x, y=y), self.assertRaises(InputError):
                point_extent_preview(self.path, x, y, convention='WGS84', crs='EPSG:4326', confirmed=True)
        self.path.write_text('经度,经度,纬度\n117,118,32\n', encoding='utf-8')
        with self.assertRaisesRegex(InputError, '重复'):
            self.build()

    def test_all_rows_checked_and_any_bad_coordinate_stops_generation(self):
        bad_pairs = [('', 31.9), ('oops', 31.9), ('NaN', 31.9), ('inf', 31.9),
                     (117.3, '-inf'), ('1e300', 31.9), (181, 31.9), (-181, 31.9),
                     (117.3, 91), (117.3, -91), (117.3, 'NaN')]
        for pair in bad_pairs:
            self.write([(117.2, 31.8)] * 1000 + [pair])
            with self.subTest(pair=pair), self.assertRaisesRegex(InputError, '1002'):
                self.build()

    def test_padding_validation(self):
        for percent in [-1, 51, 'nan', 'inf', '', ' ', True, None, object()]:
            with self.subTest(percent=percent), self.assertRaisesRegex(InputError, '留白比例'):
                self.build(padding_percent=percent)
        for percent in [0, 50, '5']:
            self.assertEqual(self.build(padding_percent=percent)[1]['padding_percent_per_side'], float(percent))

    def test_single_point_and_zero_span_get_declared_minimum(self):
        self.write([(117.2, 31.8)])
        feature, provenance = self.build(padding_percent=0)
        self.assertEqual(provenance['actual_padding_degrees_per_side'], {'longitude': .001, 'latitude': .001})
        bounds = provenance['preview_bounds_wgs84']
        self.assertGreater(bounds[2] - bounds[0], 0)
        self.assertGreater(bounds[3] - bounds[1], 0)

    def test_empty_rows_and_dateline_or_out_of_world_padding_fail(self):
        for pairs in [[], [(179.9, 32), (-179.9, 32)], [(180, 32)], [(117, 90)], [(-180, -90)]]:
            self.write(pairs)
            with self.subTest(pairs=pairs), self.assertRaises(InputError):
                self.build()

    def test_generated_feature_is_compatible_with_conservative_auto_projection(self):
        feature, _ = self.build()
        boundary = self.folder / 'point_extent_preview.geojson'
        boundary.write_text(json.dumps(feature), encoding='utf-8')
        projection, _ = auto_utm(self.path, '经度', '纬度', boundary,
                                 convention='WGS84', crs='EPSG:4326',
                                 boundary_convention='WGS84', boundary_crs='EPSG:4326')
        self.assertEqual(projection, 'EPSG:32650')

    def test_generated_preview_does_not_bypass_cross_zone_guard(self):
        self.write([(11.999, 48.1), (12.001, 48.11)])
        feature, _ = self.build()
        boundary = self.folder / 'point_extent_preview.geojson'
        boundary.write_text(json.dumps(feature), encoding='utf-8')
        with self.assertRaisesRegex(InputError, '跨 UTM'):
            auto_utm(self.path, '经度', '纬度', boundary,
                     convention='WGS84', crs='EPSG:4326',
                     boundary_convention='WGS84', boundary_crs='EPSG:4326')

    def test_auto_projection_bad_mapping_names_ranges_and_suggests_without_switching(self):
        boundary = self.folder / 'boundary.geojson'
        boundary.write_text(json.dumps({'type': 'Polygon', 'coordinates': [[[117.1,31.7],[117.5,31.7],[117.5,32.1],[117.1,32.1],[117.1,31.7]]]}), encoding='utf-8')
        for values, expected in [('1000,0.5', '没有可用于选择投影'), ('100,0.5', '覆盖范围较大')]:
            self.path.write_text('POI总数,功能混合度,lon,lat\n'+values+',117.2,31.8\n', encoding='utf-8')
            original = self.path.read_bytes()
            with self.subTest(values=values), self.assertRaises(InputError) as caught:
                auto_utm(self.path, 'POI总数', '功能混合度', boundary,
                         convention='WGS84', crs='EPSG:4326',
                         boundary_convention='WGS84', boundary_crs='EPSG:4326')
            message = str(caught.exception)
            for text in [expected, 'POI总数', '功能混合度', '数值范围', '数量 / 编号', '经纬度可能选反', 'lon / lat', '不会自动换列']:
                self.assertIn(text, message)
            self.assertEqual(original, self.path.read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
