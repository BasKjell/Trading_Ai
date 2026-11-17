import unittest

from localtrader.research import development_validation_holdout, walk_forward_splits


class ResearchTests(unittest.TestCase):
    def test_walk_forward_is_chronological_with_gaps(self) -> None:
        splits = walk_forward_splits(1000, 400, 100, purge_size=10, embargo_size=7, step_size=100)
        self.assertGreater(len(splits), 1)
        for split in splits:
            self.assertLessEqual(split.train.stop, split.purge.start)
            self.assertEqual(split.purge.stop, split.validation.start)
            self.assertEqual(split.validation.stop, split.embargo.start)

    def test_final_holdout_is_disjoint(self) -> None:
        development, holdout = development_validation_holdout(1000)
        self.assertEqual(development.stop, holdout.start)
        self.assertEqual(holdout.stop, 1000)


if __name__ == "__main__":
    unittest.main()
