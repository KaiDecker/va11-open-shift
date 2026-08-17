from __future__ import annotations

import unittest

from open_shift.drinks import (
    DrinkSubmission,
    ServiceCategory,
    classify_drink,
    evaluate_service,
    order_for_customer,
)


def submission(
    ingredients: tuple[int, int, int, int, int],
    *,
    ice: bool = False,
    aged: bool = False,
    preparation: str = "mixed",
) -> DrinkSubmission:
    return DrinkSubmission(*ingredients, ice=ice, aged=aged, preparation=preparation)


class DrinkRuleTests(unittest.TestCase):
    def test_original_recipe_is_classified_from_raw_mixer_state(self) -> None:
        drink = classify_drink(
            submission((6, 0, 3, 0, 1), aged=True, preparation="mixed")
        )
        self.assertIsNotNone(drink)
        assert drink is not None
        self.assertEqual(drink.drink_id, "btini")
        self.assertEqual(drink.display_name, "Brandtini")
        self.assertTrue(drink.alcoholic)
        self.assertFalse(drink.doubled)

    def test_exact_special_acceptable_and_wrong_are_rule_owned(self) -> None:
        order = order_for_customer("alma", 17)
        exact = evaluate_service(
            order, submission((6, 0, 3, 0, 1), aged=True)
        )
        self.assertEqual(exact.category, ServiceCategory.EXACT)

        special = evaluate_service(
            order, submission((12, 0, 6, 0, 2), aged=True)
        )
        self.assertEqual(special.category, ServiceCategory.SPECIAL)

        acceptable = evaluate_service(
            order, submission((2, 0, 1, 0, 1), preparation="mixed")
        )
        self.assertEqual(acceptable.beverage_id, "srush")
        self.assertEqual(acceptable.category, ServiceCategory.ACCEPTABLE)

        wrong = evaluate_service(
            order, submission((1, 0, 0, 0, 0), preparation="blended")
        )
        self.assertEqual(wrong.beverage_id, "failed")
        self.assertEqual(wrong.category, ServiceCategory.WRONG)

    def test_alcohol_requirement_is_checked_even_for_the_requested_drink(self) -> None:
        order = order_for_customer("dorothy", 8)
        # Piano Woman has fixed Karmotrine. A different sweet drink without it
        # is only acceptable when the order's alcohol requirement is met.
        non_alcoholic_sugar_rush = submission((2, 0, 1, 0, 0))
        result = evaluate_service(order, non_alcoholic_sugar_rush)
        self.assertEqual(result.category, ServiceCategory.WRONG)

    def test_submission_schema_rejects_client_claims_and_invalid_amounts(self) -> None:
        with self.assertRaisesRegex(ValueError, "fields"):
            DrinkSubmission.from_dict(
                {
                    "adelhyde": 2,
                    "bronson_extract": 0,
                    "powdered_delta": 1,
                    "flanergide": 0,
                    "karmotrine": 1,
                    "ice": 0,
                    "aged": 0,
                    "preparation": "mixed",
                    "claimed_result": "exact",
                }
            )
        with self.assertRaisesRegex(ValueError, "adelhyde"):
            DrinkSubmission(-1, 0, 0, 0, 0, False, False, "mixed")

    def test_gamemaker_integral_reals_are_normalized_to_integers(self) -> None:
        drink = DrinkSubmission.from_dict(
            {
                "adelhyde": 2.0,
                "bronson_extract": 0.0,
                "powdered_delta": 1.0,
                "flanergide": 0.0,
                "karmotrine": 1.0,
                "ice": 1.0,
                "aged": 0.0,
                "preparation": "mixed",
            }
        )
        self.assertEqual(drink.ingredients, (2, 0, 1, 0, 1))
        self.assertEqual(drink.to_dict()["ice"], 1)
        self.assertTrue(
            all(type(drink.to_dict()[name]) is int for name in drink.to_dict() if name != "preparation")
        )
        with self.assertRaisesRegex(ValueError, "adelhyde"):
            DrinkSubmission.from_dict(
                {
                    **drink.to_dict(),
                    "adelhyde": 1.5,
                }
            )
        with self.assertRaisesRegex(ValueError, "adelhyde"):
            DrinkSubmission.from_dict(
                {
                    **drink.to_dict(),
                    "adelhyde": True,
                }
            )


if __name__ == "__main__":
    unittest.main()
