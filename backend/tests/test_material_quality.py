import unittest

from core.material_quality import validate_material, validate_recipe


class MaterialQualityTests(unittest.TestCase):
    def test_defaults_backwards_compatible_material_fields(self):
        result = validate_material({"name": "ข้าวสาร", "unit": "กก.", "cost": 25})
        self.assertEqual(result["category"], "ingredient")
        self.assertEqual(result["purchase_unit"], "กก.")
        self.assertEqual(result["purchase_to_stock"], 1)

    def test_rejects_negative_cost_and_invalid_conversion(self):
        with self.assertRaisesRegex(ValueError, "ต้นทุนต้องไม่ติดลบ"):
            validate_material({"name": "ข้าว", "unit": "กก.", "cost": -1})
        with self.assertRaisesRegex(ValueError, "อัตราแปลงหน่วยต้องมากกว่า 0"):
            validate_material({"name": "ข้าว", "unit": "กรัม", "purchase_to_stock": 0})

    def test_recipe_rejects_duplicate_and_zero_quantity(self):
        with self.assertRaisesRegex(ValueError, "วัตถุดิบซ้ำ"):
            validate_recipe([
                {"material_id": "rice", "qty": 100},
                {"material_id": "rice", "qty": 20},
            ])
        with self.assertRaisesRegex(ValueError, "มากกว่า 0"):
            validate_recipe([{"material_id": "rice", "qty": 0}])


if __name__ == "__main__":
    unittest.main()
