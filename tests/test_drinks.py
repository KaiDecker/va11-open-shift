from __future__ import annotations

import unittest

from open_shift.drinks import (
    DRINK_RECIPES,
    DrinkSubmission,
    ServiceCategory,
    classify_drink,
    evaluate_service,
    order_for_customer,
    service_income,
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
        self.assertEqual(drink.price, 250)

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

    def test_original_recipe_prices_and_big_glass_surcharge(self) -> None:
        expected_prices = {
            "fmoai": 150, "srush": 150, "sstar": 150, "bfairy": 170,
            "fdream": 170, "scloud": 150, "moblast": 180, "gpunch": 80,
            "pdriver": 160, "splex": 160, "mablast": 170, "cspike": 140,
            "beer": 200, "bjane": 200, "fwater": 150, "btouch": 250,
            "btini": 250, "cvelvet": 280, "fweaver": 260, "meblast": 250,
            "gtemple": 220, "blight": 230, "zstar": 210, "pman": 320,
            "pwman": 320,
        }
        self.assertEqual(
            {recipe.drink_id: recipe.price for recipe in DRINK_RECIPES},
            expected_prices,
        )
        order = order_for_customer("alma", 18)
        exact_submission = submission((6, 0, 3, 0, 1), aged=True)
        special_submission = submission((12, 0, 6, 0, 2), aged=True)
        exact = evaluate_service(order, exact_submission)
        special = evaluate_service(order, special_submission)
        wrong = evaluate_service(order, submission((1, 0, 0, 0, 0), preparation="blended"))
        self.assertEqual(service_income(exact, exact_submission), 250)
        self.assertEqual(service_income(special, special_submission), 350)
        self.assertEqual(service_income(wrong, submission((1, 0, 0, 0, 0), preparation="blended")), 0)

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
