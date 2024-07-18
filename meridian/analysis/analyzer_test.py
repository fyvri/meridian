# Copyright 2024 The Meridian Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections.abc import Sequence
import os
from unittest import mock
import warnings

from absl.testing import absltest
from absl.testing import parameterized
import arviz as az
from meridian import constants
from meridian.analysis import analyzer
from meridian.analysis import test_utils
from meridian.data import test_utils as data_test_utils
from meridian.model import model
from meridian.model import prior_distribution
from meridian.model import spec
import numpy as np
import tensorflow as tf
import tensorflow_probability as tfp
import xarray as xr


_TEST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "model", "test_data"
)
_TEST_SAMPLE_PRIOR_MEDIA_AND_RF_PATH = os.path.join(
    _TEST_DIR,
    "sample_prior_media_and_rf.nc",
)
_TEST_SAMPLE_PRIOR_MEDIA_ONLY_PATH = os.path.join(
    _TEST_DIR,
    "sample_prior_media_only.nc",
)
_TEST_SAMPLE_PRIOR_RF_ONLY_PATH = os.path.join(
    _TEST_DIR,
    "sample_prior_rf_only.nc",
)
_TEST_SAMPLE_POSTERIOR_MEDIA_AND_RF_PATH = os.path.join(
    _TEST_DIR,
    "sample_posterior_media_and_rf.nc",
)
_TEST_SAMPLE_POSTERIOR_MEDIA_ONLY_PATH = os.path.join(
    _TEST_DIR,
    "sample_posterior_media_only.nc",
)
_TEST_SAMPLE_POSTERIOR_RF_ONLY_PATH = os.path.join(
    _TEST_DIR,
    "sample_posterior_rf_only.nc",
)
_TEST_SAMPLE_TRACE_PATH = os.path.join(
    _TEST_DIR,
    "sample_trace.nc",
)

# Data dimensions for sample input.
_N_CHAINS = 2
_N_KEEP = 10
_N_DRAWS = 10
_N_GEOS = 5
_N_TIMES = 49
_N_MEDIA_TIMES = 52
_N_CONTROLS = 2
_N_MEDIA_CHANNELS = 3
_N_RF_CHANNELS = 2


def _convert_with_swap(array: xr.DataArray) -> tf.Tensor:
  """Converts DataArray to tf.Tensor and swaps first two dimensions."""
  tensor = tf.convert_to_tensor(array)
  perm = [1, 0] + [i for i in range(2, len(tensor.shape))]
  return tf.transpose(tensor, perm=perm)


def _build_inference_data(
    prior_path: str, posterior_path: str
) -> az.InferenceData:
  inference_data = az.InferenceData(
      prior=xr.open_dataset(prior_path),
      posterior=xr.open_dataset(posterior_path),
  )
  inference_data.groups = lambda: [
      constants.PRIOR,
      constants.POSTERIOR,
  ]
  return inference_data


class AnalyzerTest(tf.test.TestCase, parameterized.TestCase):

  @classmethod
  def setUpClass(cls):
    super(AnalyzerTest, cls).setUpClass()
    # Input data resulting in revenue computation.
    cls.input_data_media_and_rf = (
        data_test_utils.sample_input_data_non_revenue_revenue_per_kpi(
            n_geos=_N_GEOS,
            n_times=_N_TIMES,
            n_media_times=_N_MEDIA_TIMES,
            n_controls=_N_CONTROLS,
            n_media_channels=_N_MEDIA_CHANNELS,
            n_rf_channels=_N_RF_CHANNELS,
            seed=0,
        )
    )
    model_spec = spec.ModelSpec(max_lag=15)
    cls.meridian_media_and_rf = model.Meridian(
        input_data=cls.input_data_media_and_rf, model_spec=model_spec
    )
    cls.analyzer_media_and_rf = analyzer.Analyzer(cls.meridian_media_and_rf)

    cls.inference_data_media_and_rf = _build_inference_data(
        _TEST_SAMPLE_PRIOR_MEDIA_AND_RF_PATH,
        _TEST_SAMPLE_POSTERIOR_MEDIA_AND_RF_PATH,
    )
    cls.enter_context(
        mock.patch.object(
            model.Meridian,
            "inference_data",
            new=property(lambda unused_self: cls.inference_data_media_and_rf),
        )
    )

  def test_get_mean_and_ci(self):
    data = np.array([[[10.0, 7, 4], [3, 2, 1]], [[1, 2, 3], [4, 5, 6.0]]])
    result = analyzer.get_mean_and_ci(data, confidence_level=0.9)
    np.testing.assert_allclose(
        result,
        np.array([[4.5, 1.3, 9.1], [4, 2, 6.7], [3.5, 1.3, 5.7]]),
        atol=0.1,
    )

  def test_expected_impact_wrong_controls_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_controls.shape must match controls.shape",
    ):
      self.analyzer_media_and_rf.expected_impact(
          new_controls=self.meridian_media_and_rf.population,
      )

  def test_expected_impact_wrong_media_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_media.shape must match media.shape",
    ):
      self.analyzer_media_and_rf.expected_impact(
          new_media=self.meridian_media_and_rf.population,
      )

  def test_expected_impact_wrong_reach_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_reach.shape must match reach.shape",
    ):
      self.analyzer_media_and_rf.expected_impact(
          new_reach=self.meridian_media_and_rf.population,
      )

  def test_expected_impact_wrong_frequency_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_frequency.shape must match frequency.shape",
    ):
      self.analyzer_media_and_rf.expected_impact(
          new_frequency=self.meridian_media_and_rf.population,
      )

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      geos_to_include=[None, ["geo_1", "geo_3"]],
      times_to_include=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_expected_impact_media_and_rf_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      geos_to_include: Sequence[str] | None,
      times_to_include: Sequence[str] | None,
  ):
    impact = self.analyzer_media_and_rf.expected_impact(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=geos_to_include,
        selected_times=times_to_include,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(geos_to_include),) if geos_to_include is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(times_to_include),)
          if times_to_include is not None
          else (_N_TIMES,)
      )
    self.assertEqual(impact.shape, expected_shape)

  def test_incremental_impact_wrong_media_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_media.shape must match media.shape",
    ):
      self.analyzer_media_and_rf.incremental_impact(
          new_media=self.meridian_media_and_rf.population,
      )

  def test_incremental_impact_wrong_reach_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_reach.shape must match reach.shape",
    ):
      self.analyzer_media_and_rf.incremental_impact(
          new_reach=self.meridian_media_and_rf.population,
      )

  def test_incremental_impact_wrong_frequency_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_frequency.shape must match frequency.shape",
    ):
      self.analyzer_media_and_rf.incremental_impact(
          new_frequency=self.meridian_media_and_rf.population,
      )

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_incremental_impact_media_and_rf_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    impact = self.analyzer_media_and_rf.incremental_impact(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_MEDIA_CHANNELS + _N_RF_CHANNELS,)
    self.assertEqual(impact.shape, expected_shape)

  # The purpose of this test is to prevent accidental logic change.
  @parameterized.named_parameters(
      dict(
          testcase_name="use_prior",
          use_posterior=False,
          expected_impact=test_utils.INC_IMPACT_MEDIA_AND_RF_USE_PRIOR,
      ),
      dict(
          testcase_name="use_posterior",
          use_posterior=True,
          expected_impact=test_utils.INC_IMPACT_MEDIA_AND_RF_USE_POSTERIOR,
      ),
  )
  def test_incremental_impact_media_and_rf(
      self,
      use_posterior: bool,
      expected_impact: np.ndarray,
  ):
    model.Meridian.inference_data = mock.PropertyMock(
        return_value=self.inference_data_media_and_rf
    )
    impact = self.analyzer_media_and_rf.incremental_impact(
        use_posterior=use_posterior,
    )
    self.assertAllClose(
        impact,
        tf.convert_to_tensor(expected_impact),
        rtol=1e-3,
        atol=1e-3,
    )

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
      by_reach=[False, True],
  )
  def test_marginal_roi_media_and_rf_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
      by_reach: bool,
  ):
    type(self.meridian_media_and_rf).inference_data = mock.PropertyMock(
        return_value=self.inference_data_media_and_rf
    )
    mroi = self.analyzer_media_and_rf.marginal_roi(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
        by_reach=by_reach,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_MEDIA_CHANNELS + _N_RF_CHANNELS,)
    self.assertEqual(mroi.shape, expected_shape)

  # The purpose of this test is to prevent accidental logic change.
  @parameterized.named_parameters(
      dict(
          testcase_name="use_prior",
          use_posterior=False,
          by_reach=False,
          expected_mroi=test_utils.MROI_MEDIA_AND_RF_USE_PRIOR,
      ),
      dict(
          testcase_name="use_prior_by_reach",
          use_posterior=False,
          by_reach=True,
          expected_mroi=test_utils.MROI_MEDIA_AND_RF_USE_PRIOR_BY_REACH,
      ),
      dict(
          testcase_name="use_posterior",
          use_posterior=True,
          by_reach=False,
          expected_mroi=test_utils.MROI_MEDIA_AND_RF_USE_POSTERIOR,
      ),
      dict(
          testcase_name="use_posterior_by_reach",
          use_posterior=True,
          by_reach=True,
          expected_mroi=test_utils.MROI_MEDIA_AND_RF_USE_POSTERIOR_BY_REACH,
      ),
  )
  def test_marginal_roi_media_and_rf(
      self,
      use_posterior: bool,
      by_reach: bool,
      expected_mroi: tuple[float, ...],
  ):
    model.Meridian.inference_data = mock.PropertyMock(
        return_value=self.inference_data_media_and_rf
    )
    mroi = self.analyzer_media_and_rf.marginal_roi(
        by_reach=by_reach,
        use_posterior=use_posterior,
    )
    self.assertAllClose(
        mroi,
        tf.convert_to_tensor(expected_mroi),
        rtol=1e-3,
        atol=1e-3,
    )

  def test_roi_wrong_media_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_media.shape must match media.shape",
    ):
      self.analyzer_media_and_rf.roi(
          new_media=self.meridian_media_and_rf.population,
      )

  def test_roi_wrong_media_spend_raises_exception(self):
    with self.assertRaisesWithLiteralMatch(
        ValueError,
        "new_media_spend.shape: (5,) must match either (3,) or (5, 49, 3).",
    ):
      self.analyzer_media_and_rf.roi(
          new_media_spend=self.meridian_media_and_rf.population,
      )

  def test_roi_wrong_reach_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_reach.shape must match reach.shape",
    ):
      self.analyzer_media_and_rf.roi(
          new_reach=self.meridian_media_and_rf.population,
      )

  def test_roi_wrong_frequency_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "new_frequency.shape must match frequency.shape",
    ):
      self.analyzer_media_and_rf.roi(
          new_frequency=self.meridian_media_and_rf.population,
      )

  def test_roi_wrong_rf_spend_raises_exception(self):
    with self.assertRaisesWithLiteralMatch(
        ValueError,
        "new_rf_spend.shape: (5,) must match either (2,) or (5, 49, 2).",
    ):
      self.analyzer_media_and_rf.roi(
          new_rf_spend=self.meridian_media_and_rf.population,
      )

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_roi_media_and_rf_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    roi = self.analyzer_media_and_rf.roi(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_MEDIA_CHANNELS + _N_RF_CHANNELS,)
    self.assertEqual(roi.shape, expected_shape)

  def test_roi_media_and_rf_default_returns_correct_value(self):
    roi = self.analyzer_media_and_rf.roi()
    total_spend = (
        self.analyzer_media_and_rf.filter_and_aggregate_geos_and_times(
            self.meridian_media_and_rf.total_spend
        )
    )
    expected_roi = self.analyzer_media_and_rf.incremental_impact() / total_spend
    self.assertAllClose(expected_roi, roi)

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_cpik_media_and_rf_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    cpik = self.analyzer_media_and_rf.cpik(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_MEDIA_CHANNELS + _N_RF_CHANNELS,)
    self.assertEqual(cpik.shape, expected_shape)

  def test_cpik_media_and_rf_default_returns_correct_value(self):
    cpik = self.analyzer_media_and_rf.cpik()
    total_spend = (
        self.analyzer_media_and_rf.filter_and_aggregate_geos_and_times(
            self.meridian_media_and_rf.total_spend
        )
    )
    expected_cpik = total_spend / self.analyzer_media_and_rf.incremental_impact(
        use_kpi=True
    )
    self.assertAllClose(expected_cpik, cpik)

  def test_hill_histogram_column_names(self):
    hill_histogram_table = (
        self.analyzer_media_and_rf._get_hill_histogram_dataframe(n_bins=25)
    )
    self.assertEqual(
        list(hill_histogram_table.columns),
        [
            constants.CHANNEL,
            constants.CHANNEL_TYPE,
            constants.SCALED_COUNT_HISTOGRAM,
            constants.COUNT_HISTOGRAM,
            constants.START_INTERVAL_HISTOGRAM,
            constants.END_INTERVAL_HISTOGRAM,
        ],
    )

  def test_hill_calculation_dataframe_properties(self):
    hill_table = self.analyzer_media_and_rf.hill_curves()

    self.assertEqual(
        list(hill_table.columns),
        [
            constants.CHANNEL,
            constants.MEDIA_UNITS,
            constants.DISTRIBUTION,
            constants.CI_HI,
            constants.CI_LO,
            constants.MEAN,
            constants.CHANNEL_TYPE,
            constants.SCALED_COUNT_HISTOGRAM,
            constants.COUNT_HISTOGRAM,
            constants.START_INTERVAL_HISTOGRAM,
            constants.END_INTERVAL_HISTOGRAM,
        ],
    )
    self.assertAllInSet(
        list(set(hill_table[constants.CHANNEL])),
        ["ch_0", "ch_2", "ch_1", "rf_ch_0", "rf_ch_1"],
    )
    ci_lo_col = list(hill_table[constants.CI_LO].notna())
    ci_hi_col = list(hill_table[constants.CI_HI].notna())
    mean_col = list(hill_table[constants.MEAN].notna())

    for i, e in enumerate(mean_col):
      ci_lo_val = ci_lo_col[i]
      ci_hi_val = ci_hi_col[i]
      self.assertGreaterEqual(e, ci_lo_val)
      self.assertLessEqual(e, ci_hi_val)

  def test_hill_calculation_curve_data_correct(self):
    hill_table = self.analyzer_media_and_rf.hill_curves()
    self.assertAllClose(
        list(hill_table[constants.CI_HI])[:5],
        np.array(test_utils.HILL_CURVES_CI_HI),
        atol=1e-5,
    )
    self.assertAllClose(
        list(hill_table[constants.CI_LO])[:5],
        np.array(test_utils.HILL_CURVES_CI_LO),
        atol=1e-5,
    )
    self.assertAllClose(
        list(hill_table[constants.MEAN])[:5],
        np.array(test_utils.HILL_CURVES_MEAN),
        atol=1e-5,
    )

  def test_hill_calculation_histogram_data_correct(self):
    hill_table = self.analyzer_media_and_rf.hill_curves()
    # The Histogram data is in the bottom portion of the DataFrame for
    # Altair plotting purposes.
    self.assertAllClose(
        list(hill_table[constants.SCALED_COUNT_HISTOGRAM])[-5:],
        np.array(test_utils.HILL_CURVES_SCALED_COUNT_HISTOGRAM),
        atol=1e-5,
    )
    self.assertAllClose(
        list(hill_table[constants.COUNT_HISTOGRAM])[-5:],
        np.array(test_utils.HILL_CURVES_COUNT_HISTOGRAM),
        atol=1e-5,
    )
    self.assertAllClose(
        list(hill_table[constants.START_INTERVAL_HISTOGRAM])[-5:],
        np.array(test_utils.HILL_CURVES_START_INTERVAL_HISTOGRAM),
        atol=1e-5,
    )
    self.assertAllClose(
        list(hill_table[constants.END_INTERVAL_HISTOGRAM])[-5:],
        np.array(test_utils.HILL_CURVES_END_INTERVAL_HISTOGRAM),
        atol=1e-5,
    )

  def test_media_summary_returns_correct_values(self):
    media_summary = self.analyzer_media_and_rf.media_summary_metrics(
        confidence_level=0.9,
        marginal_roi_by_reach=False,
        aggregate_geos=True,
        aggregate_times=True,
        selected_geos=None,
        selected_times=None,
    )
    self.assertIsNotNone(media_summary.impressions)
    self.assertIsNotNone(media_summary.spend)
    self.assertIsNotNone(media_summary.pct_of_spend)
    self.assertIsNotNone(media_summary.cpm)
    self.assertIsNotNone(media_summary.incremental_impact)
    self.assertIsNotNone(media_summary.pct_of_contribution)
    self.assertIsNotNone(media_summary.roi)
    self.assertIsNotNone(media_summary.effectiveness)
    self.assertIsNotNone(media_summary.mroi)
    self.assertAllClose(
        media_summary.impressions, test_utils.SAMPLE_IMPRESSIONS
    )
    self.assertAllClose(
        media_summary.pct_of_impressions, test_utils.SAMPLE_PCT_OF_IMPRESSIONS
    )
    self.assertAllClose(
        media_summary.spend, test_utils.SAMPLE_SPEND, atol=1e-2, rtol=1e-2
    )
    self.assertAllClose(
        media_summary.pct_of_spend,
        test_utils.SAMPLE_PCT_OF_SPEND,
        atol=1e-2,
        rtol=1e-2,
    )
    self.assertAllClose(
        media_summary.cpm,
        test_utils.SAMPLE_CPM,
    )
    self.assertAllClose(
        media_summary.incremental_impact,
        test_utils.SAMPLE_INCREMENTAL_IMPACT,
        atol=1e-2,
        rtol=1e-2,
    )
    self.assertAllClose(
        media_summary.pct_of_contribution,
        test_utils.SAMPLE_PCT_OF_CONTRIBUTION,
        atol=1e-2,
        rtol=1e-2,
    )
    self.assertAllClose(
        media_summary.roi, test_utils.SAMPLE_ROI, atol=1e-3, rtol=1e-3
    )
    self.assertAllClose(
        media_summary.effectiveness,
        test_utils.SAMPLE_EFFECTIVENESS,
        atol=1e-3,
        rtol=1e-3,
    )
    self.assertAllClose(
        media_summary.mroi, test_utils.SAMPLE_MROI, atol=1e-3, rtol=1e-3
    )

  @parameterized.product(
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_media_summary_returns_correct_shapes(
      self,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    analyzer_ = self.analyzer_media_and_rf
    num_channels = _N_MEDIA_CHANNELS + _N_RF_CHANNELS

    media_summary = analyzer_.media_summary_metrics(
        confidence_level=0.8,
        marginal_roi_by_reach=False,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_channel_shape = ()
    if not aggregate_geos:
      expected_channel_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_channel_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )

    # (ch_1, ch_2, ..., Total_media, [mean, ci_lo, ci_hi], [prior, posterior])
    expected_channel_shape += (num_channels + 1,)
    expected_shape = expected_channel_shape + (
        3,
        2,
    )
    self.assertEqual(media_summary.impressions.shape, expected_channel_shape)
    self.assertEqual(
        media_summary.pct_of_impressions.shape, expected_channel_shape
    )
    self.assertEqual(media_summary.spend.shape, expected_channel_shape)
    self.assertEqual(media_summary.pct_of_spend.shape, expected_channel_shape)
    self.assertEqual(media_summary.cpm.shape, expected_channel_shape)
    self.assertEqual(media_summary.incremental_impact.shape, expected_shape)
    self.assertEqual(media_summary.pct_of_contribution.shape, expected_shape)
    self.assertEqual(media_summary.roi.shape, expected_shape)
    self.assertEqual(media_summary.effectiveness.shape, expected_shape)
    self.assertEqual(media_summary.mroi.shape, expected_shape)

  def test_optimal_frequency_data_media_and_rf_correct(self):
    actual = self.analyzer_media_and_rf.optimal_freq(
        freq_grid=[1.0, 2.0, 3.0],
        confidence_level=0.9,
        use_posterior=True,
    )
    expected = xr.Dataset(
        coords={
            constants.FREQUENCY: ([constants.FREQUENCY], [1.0, 2.0, 3.0]),
            constants.RF_CHANNEL: (
                [constants.RF_CHANNEL],
                ["rf_ch_0", "rf_ch_1"],
            ),
            constants.METRIC: (
                [constants.METRIC],
                [constants.MEAN, constants.CI_LO, constants.CI_HI],
            ),
        },
        data_vars={
            constants.ROI: (
                [constants.FREQUENCY, constants.RF_CHANNEL, constants.METRIC],
                [  # rf_ch_0.           # rf_ch_1
                    [[4.57, 1.60, 7.52], [6.60, 1.69, 11.70]],  # freq=1.0
                    [[2.47, 0.97, 3.97], [3.66, 1.09, 6.32]],  # freq=2.0
                    [[1.72, 0.71, 2.73], [2.53, 0.82, 4.30]],  # freq=3.0
                ],
            ),
            constants.OPTIMAL_FREQUENCY: ([constants.RF_CHANNEL], [1.0, 1.0]),
            constants.OPTIMIZED_INCREMENTAL_IMPACT: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[313.0, 130.1, 495.3], [454.7, 159.1, 761.4]],
            ),
            constants.OPTIMIZED_EFFECTIVENESS: (
                [constants.RF_CHANNEL, constants.METRIC],
                [
                    [0.000427, 0.000177, 0.000676],
                    [0.000618, 0.000216, 0.001036],
                ],
            ),
            constants.OPTIMIZED_PCT_OF_CONTRIBUTION: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[1.44, 0.59, 2.27], [2.09, 0.73, 3.50]],
            ),
            constants.OPTIMIZED_ROI: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[1.150, 0.478, 1.819], [1.579, 0.552, 2.644]],
            ),
            constants.OPTIMIZED_MROI_BY_REACH: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[1.150, 0.478, 1.819], [1.579, 0.552, 2.645]],
            ),
            constants.OPTIMIZED_MROI_BY_FREQUENCY: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[0.118, 0.095, 0.139], [0.152, 0.141, 0.164]],
            ),
        },
        attrs={
            constants.CONFIDENCE_LEVEL: 0.9,
            "use_posterior": True,
        },
    )

    xr.testing.assert_allclose(actual, expected, atol=0.1)
    xr.testing.assert_allclose(actual.frequency, expected.frequency, atol=0.1)
    xr.testing.assert_allclose(actual.rf_channel, expected.rf_channel, atol=0.1)
    xr.testing.assert_allclose(actual.metric, expected.metric, atol=0.1)
    xr.testing.assert_allclose(actual.roi, expected.roi, atol=0.1)
    xr.testing.assert_allclose(
        actual.optimal_frequency, expected.optimal_frequency, atol=0.1
    )
    xr.testing.assert_allclose(
        actual.optimized_incremental_impact,
        expected.optimized_incremental_impact,
        atol=0.1,
    )
    xr.testing.assert_allclose(
        actual.optimized_effectiveness,
        expected.optimized_effectiveness,
        atol=0.00001,
    )
    xr.testing.assert_allclose(
        actual.optimized_pct_of_contribution,
        expected.optimized_pct_of_contribution,
        atol=0.01,
    )
    xr.testing.assert_allclose(
        actual.optimized_roi,
        expected.optimized_roi,
        atol=0.01,
    )
    xr.testing.assert_allclose(
        actual.optimized_mroi_by_reach,
        expected.optimized_mroi_by_reach,
        atol=0.01,
    )
    xr.testing.assert_allclose(
        actual.optimized_mroi_by_frequency,
        expected.optimized_mroi_by_frequency,
        atol=0.01,
    )
    self.assertEqual(actual.confidence_level, 0.9)
    self.assertEqual(actual.use_posterior, expected.use_posterior)

  def test_r_hat_summary_media_and_rf_correct(self):
    r_hat_summary = self.analyzer_media_and_rf.r_hat_summary()
    self.assertEqual(r_hat_summary.shape, (20, 7))
    self.assertSetEqual(
        set(r_hat_summary.param),
        set(
            constants.COMMON_PARAMETER_NAMES
            + constants.MEDIA_PARAMETER_NAMES
            + constants.RF_PARAMETER_NAMES
        )
        - set([constants.SLOPE_M]),
    )

  def test_predictive_accuracy_without_holdout_id_columns_correct(self):
    predictive_accuracy_dataset = (
        self.analyzer_media_and_rf.predictive_accuracy()
    )

    self.assertListEqual(
        list(predictive_accuracy_dataset[constants.METRIC].values),
        [constants.R_SQUARED, constants.MAPE, constants.WMAPE],
    )
    self.assertListEqual(
        list(predictive_accuracy_dataset[constants.GEO_GRANULARITY].values),
        [constants.GEO, constants.NATIONAL],
    )
    df = (
        predictive_accuracy_dataset[constants.VALUE]
        .to_dataframe()
        .reset_index()
    )
    self.assertListEqual(
        list(df.columns),
        [constants.METRIC, constants.GEO_GRANULARITY, constants.VALUE],
    )

  @mock.patch.object(
      model.Meridian, "is_national", new=property(lambda unused_self: True)
  )
  def test_predictive_accuracy_national(self):
    predictive_accuracy_dataset = (
        self.analyzer_media_and_rf.predictive_accuracy()
    )
    self.assertListEqual(
        list(predictive_accuracy_dataset[constants.GEO_GRANULARITY].values),
        [constants.NATIONAL],
    )

  @parameterized.product(
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_predictive_accuracy_without_holdout_id_parameterized(
      self,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    predictive_accuracy_dims_kwargs = {
        "selected_geos": selected_geos,
        "selected_times": selected_times,
    }
    predictive_accuracy_dataset = (
        self.analyzer_media_and_rf.predictive_accuracy(
            **predictive_accuracy_dims_kwargs,
        )
    )
    df = (
        predictive_accuracy_dataset[constants.VALUE]
        .to_dataframe()
        .reset_index()
    )

    if not selected_geos and not selected_times:
      self.assertAllClose(
          list(df[constants.VALUE]),
          test_utils.PREDICTIVE_ACCURACY_NO_HOLDOUT_ID_NO_GEOS_OR_TIMES,
          atol=1e-3,
      )
    elif selected_geos and not selected_times:
      self.assertAllClose(
          list(df[constants.VALUE]),
          test_utils.PREDICTIVE_ACCURACY_NO_HOLDOUT_ID_GEOS_NO_TIMES,
          atol=1e-3,
      )
    elif not selected_geos and selected_times:
      self.assertAllClose(
          list(df[constants.VALUE]),
          test_utils.PREDICTIVE_ACCURACY_NO_HOLDOUT_ID_TIMES_NO_GEOS,
          atol=1e-3,
      )
    else:
      self.assertAllClose(
          list(df[constants.VALUE]),
          test_utils.PREDICTIVE_ACCURACY_NO_HOLDOUT_ID_TIMES_AND_GEOS,
          atol=1e-3,
      )

  def test_predictive_accuracy_with_holdout_id_table_properties_correct(self):
    n_geos = self.meridian_media_and_rf.n_geos
    n_times = self.meridian_media_and_rf.n_times
    holdout_id = np.full([n_geos, n_times], False)
    for i in range(n_geos):
      holdout_id[i, np.random.choice(n_times, int(np.round(0.2 * n_times)))] = (
          True
      )
    model_spec = spec.ModelSpec(holdout_id=holdout_id)
    meridian = model.Meridian(
        model_spec=model_spec, input_data=self.input_data_media_and_rf
    )
    analyzer_holdout_id = analyzer.Analyzer(meridian)
    predictive_accuracy_dataset = analyzer_holdout_id.predictive_accuracy()
    df = (
        predictive_accuracy_dataset[constants.VALUE]
        .to_dataframe()
        .reset_index()
    )
    self.assertListEqual(
        list(df.columns),
        [
            constants.METRIC,
            constants.GEO_GRANULARITY,
            constants.EVALUATION_SET_VAR,
            constants.VALUE,
        ],
    )

  def test_predictive_accuracy_holdout_id_values_correct(self):
    n_geos = len(self.input_data_media_and_rf.geo)
    n_times = len(self.input_data_media_and_rf.time)
    holdout_id = np.full([n_geos, n_times], False)
    for i in range(n_geos):
      holdout_id[i, np.random.choice(n_times, int(np.round(0.2 * n_times)))] = (
          True
      )
    model_spec = spec.ModelSpec(holdout_id=holdout_id)
    meridian = model.Meridian(
        model_spec=model_spec, input_data=self.input_data_media_and_rf
    )
    analyzer_holdout_id = analyzer.Analyzer(meridian)
    predictive_accuracy_dataset = analyzer_holdout_id.predictive_accuracy()
    df = (
        predictive_accuracy_dataset[constants.VALUE]
        .to_dataframe()
        .reset_index()
    )
    self.assertAllClose(
        list(df[constants.VALUE]),
        test_utils.PREDICTIVE_ACCURACY_HOLDOUT_ID,
        atol=2e-3,
    )

  def test_response_curves_check_both_channel_types_returns_correct_spend(self):
    response_curve_data = self.analyzer_media_and_rf.response_curves(
        by_reach=False
    )
    response_data_spend = response_curve_data.spend.values

    media_summary_spend = self.analyzer_media_and_rf.media_summary_metrics(
        confidence_level=0.9,
        marginal_roi_by_reach=False,
    ).spend[:-1]
    self.assertAllEqual(
        media_summary_spend * 2,
        response_data_spend[-1],
    )

  @parameterized.named_parameters(
      dict(
          testcase_name="default",
          aggregate_geos=False,
          aggregate_times=False,
          holdout_id=None,
          split_by_holdout_id=False,
          expected_shape=(
              _N_GEOS,
              _N_TIMES,
              3,  # [mean, ci_lo, ci_hi]
          ),
          expected_actual_shape=(_N_GEOS, _N_TIMES),
      ),
      dict(
          testcase_name="split_by_holdout_id_true_wo_holdout_id",
          aggregate_geos=False,
          aggregate_times=False,
          holdout_id=None,
          split_by_holdout_id=True,
          expected_shape=(
              _N_GEOS,
              _N_TIMES,
              3,  # [mean, ci_lo, ci_hi]
          ),
          expected_actual_shape=(_N_GEOS, _N_TIMES),
      ),
      dict(
          testcase_name="split_by_holdout_id_true_w_holdout_id",
          aggregate_geos=False,
          aggregate_times=False,
          holdout_id=np.random.choice([True, False], size=(_N_GEOS, _N_TIMES)),
          split_by_holdout_id=True,
          expected_shape=(
              _N_GEOS,
              _N_TIMES,
              3,  # [mean, ci_lo, ci_hi]
              3,  # [train, test, all]
          ),
          expected_actual_shape=(_N_GEOS, _N_TIMES),
      ),
      dict(
          testcase_name="split_by_holdout_id_false_w_holdout_id",
          aggregate_geos=False,
          aggregate_times=False,
          holdout_id=np.random.choice([True, False], size=(_N_GEOS, _N_TIMES)),
          split_by_holdout_id=False,
          expected_shape=(
              _N_GEOS,
              _N_TIMES,
              3,  # [mean, ci_lo, ci_hi]
          ),
          expected_actual_shape=(_N_GEOS, _N_TIMES),
      ),
      dict(
          testcase_name="aggregate_geos_wo_split",
          aggregate_geos=True,
          aggregate_times=False,
          holdout_id=None,
          split_by_holdout_id=False,
          expected_shape=(
              _N_TIMES,
              3,  # [mean, ci_lo, ci_hi]
          ),
          expected_actual_shape=(_N_TIMES,),
      ),
      dict(
          testcase_name="aggregate_geos_w_split",
          aggregate_geos=True,
          aggregate_times=False,
          holdout_id=np.random.choice([True, False], size=(_N_GEOS, _N_TIMES)),
          split_by_holdout_id=True,
          expected_shape=(
              _N_TIMES,
              3,  # [mean, ci_lo, ci_hi]
              3,  # [train, test, all]
          ),
          expected_actual_shape=(_N_TIMES,),
      ),
      dict(
          testcase_name="aggregate_times_wo_split",
          aggregate_geos=False,
          aggregate_times=True,
          holdout_id=None,
          split_by_holdout_id=False,
          expected_shape=(
              _N_GEOS,
              3,  # [mean, ci_lo, ci_hi]
          ),
          expected_actual_shape=(_N_GEOS,),
      ),
      dict(
          testcase_name="aggregate_times_w_split",
          aggregate_geos=False,
          aggregate_times=True,
          holdout_id=np.random.choice([True, False], size=(_N_GEOS, _N_TIMES)),
          split_by_holdout_id=True,
          expected_shape=(
              _N_GEOS,
              3,  # [mean, ci_lo, ci_hi]
              3,  # [train, test, all]
          ),
          expected_actual_shape=(_N_GEOS,),
      ),
      dict(
          testcase_name="aggregate_geos_and_times_wo_split",
          aggregate_geos=True,
          aggregate_times=True,
          holdout_id=None,
          split_by_holdout_id=False,
          expected_shape=(3,),  # [mean, ci_lo, ci_hi]
          expected_actual_shape=(),
      ),
      dict(
          testcase_name="aggregate_geos_and_times_w_split",
          aggregate_geos=True,
          aggregate_times=True,
          holdout_id=np.random.choice([True, False], size=(_N_GEOS, _N_TIMES)),
          split_by_holdout_id=True,
          expected_shape=(
              3,  # [mean, ci_lo, ci_hi]
              3,  # [train, test, all]
          ),
          expected_actual_shape=(),
      ),
  )
  def test_expected_vs_actual_correct_data(
      self,
      aggregate_geos: bool,
      aggregate_times: bool,
      holdout_id: np.ndarray | None,
      split_by_holdout_id: bool,
      expected_shape: tuple[int, ...],
      expected_actual_shape: tuple[int, ...],
  ):
    model_spec = spec.ModelSpec(holdout_id=holdout_id)
    meridian = model.Meridian(
        model_spec=model_spec, input_data=self.input_data_media_and_rf
    )
    meridian_analyzer = analyzer.Analyzer(meridian)

    ds = meridian_analyzer.expected_vs_actual_data(
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        split_by_holdout_id=split_by_holdout_id,
    )

    expected_actual_values = (
        meridian.kpi
        if self.input_data_media_and_rf.revenue_per_kpi is None
        else meridian.kpi * self.input_data_media_and_rf.revenue_per_kpi
    )  # shape (n_geos, n_times)

    axis_to_sum = tuple(
        ([0] if aggregate_geos else []) + ([1] if aggregate_times else [])
    )
    expected_actual_values = np.sum(expected_actual_values, axis=axis_to_sum)

    if aggregate_geos:
      self.assertNotIn(constants.GEO, ds.coords)
    else:
      self.assertListEqual(
          list(ds.geo.values), list(self.input_data_media_and_rf.geo.values)
      )

    if aggregate_times:
      self.assertNotIn(constants.TIME, ds.coords)
    else:
      self.assertListEqual(
          list(ds.time.values), list(self.input_data_media_and_rf.time.values)
      )

    self.assertListEqual(
        list(ds.metric.values),
        [constants.MEAN, constants.CI_LO, constants.CI_HI],
    )

    self.assertEqual(ds.expected.shape, expected_shape)
    self.assertEqual(ds.baseline.shape, expected_shape)
    self.assertEqual(ds.actual.shape, expected_actual_shape)
    self.assertEqual(ds.confidence_level, 0.9)

    np.testing.assert_array_less(
        ds.expected.sel(metric=constants.MEAN),
        ds.expected.sel(metric=constants.CI_HI),
    )
    np.testing.assert_array_less(
        ds.expected.sel(metric=constants.CI_LO),
        ds.expected.sel(metric=constants.MEAN),
    )
    np.testing.assert_array_less(
        ds.baseline.sel(metric=constants.MEAN),
        ds.baseline.sel(metric=constants.CI_HI),
    )
    np.testing.assert_array_less(
        ds.baseline.sel(metric=constants.CI_LO),
        ds.baseline.sel(metric=constants.MEAN),
    )
    np.testing.assert_array_less(ds.baseline, ds.expected)

    # Test the math for a sample of the actual impact metrics.
    self.assertAllClose(
        ds.actual.values,
        expected_actual_values,
        atol=1e-5,
    )

  def test_expected_vs_actual_warns_if_split_by_holdout_id_without_holdout_id(
      self,
  ):
    with warnings.catch_warnings(record=True) as w:
      warnings.simplefilter("always")
      self.analyzer_media_and_rf.expected_vs_actual_data(
          split_by_holdout_id=True
      )

      self.assertLen(w, 1)
      self.assertTrue(issubclass(w[0].category, UserWarning))
      self.assertIn(
          "`split_by_holdout_id` is True but `holdout_id` is `None`. Data will"
          " not be split.",
          str(w[0].message),
      )

  def test_adstock_decay_dataframe(self):
    adstock_decay_dataframe = self.analyzer_media_and_rf.adstock_decay(
        confidence_level=0.9
    )

    self.assertEqual(
        list(adstock_decay_dataframe.columns),
        [
            constants.CHANNEL,
            constants.TIME_UNITS,
            constants.DISTRIBUTION,
            constants.CI_HI,
            constants.CI_LO,
            constants.MEAN,
            constants.IS_INT_TIME_UNIT,
        ],
    )
    self.assertAllInSet(
        list(set(adstock_decay_dataframe[constants.CHANNEL])),
        ["rf_ch_0", "rf_ch_1", "ch_0", "ch_1", "ch_2"],
    )
    for i, e in enumerate(list(adstock_decay_dataframe[constants.MEAN])):
      self.assertGreaterEqual(
          e, list(adstock_decay_dataframe[constants.CI_LO])[i]
      )
      self.assertLessEqual(e, list(adstock_decay_dataframe[constants.CI_HI])[i])

  def test_adstock_decay_effect_values(self):
    adstock_decay_dataframe = self.analyzer_media_and_rf.adstock_decay(
        confidence_level=0.9
    )

    first_channel = adstock_decay_dataframe[constants.CHANNEL].iloc[0]
    first_channel_df = adstock_decay_dataframe[
        adstock_decay_dataframe[constants.CHANNEL] == first_channel
    ]
    prior_df = first_channel_df[
        first_channel_df[constants.DISTRIBUTION] == constants.PRIOR
    ]
    posterior_df = first_channel_df[
        first_channel_df[constants.DISTRIBUTION] == constants.POSTERIOR
    ]

    mean_arr_prior = list(prior_df[constants.MEAN])
    ci_lo_arr_prior = list(prior_df[constants.CI_LO])
    ci_hi_arr_prior = list(prior_df[constants.CI_HI])

    mean_arr_posterior = list(posterior_df[constants.MEAN])
    ci_lo_arr_posterior = list(posterior_df[constants.CI_LO])
    ci_hi_arr_posterior = list(posterior_df[constants.CI_HI])

    # Make sure values are monotonically decreasing throughout DataFrame slice
    # for one channel.
    for i in range(len(mean_arr_prior) - 1):
      self.assertLessEqual(mean_arr_prior[i + 1], mean_arr_prior[i])
      self.assertLessEqual(ci_lo_arr_prior[i + 1], ci_lo_arr_prior[i])
      self.assertLessEqual(ci_hi_arr_prior[i + 1], ci_hi_arr_prior[i])

      self.assertLessEqual(mean_arr_posterior[i + 1], mean_arr_posterior[i])
      self.assertLessEqual(ci_lo_arr_posterior[i + 1], ci_lo_arr_posterior[i])
      self.assertLessEqual(ci_hi_arr_posterior[i + 1], ci_hi_arr_posterior[i])

  def test_adstock_decay_math_correct(self):
    adstock_decay_dataframe = self.analyzer_media_and_rf.adstock_decay(
        confidence_level=0.9
    )

    first_channel = adstock_decay_dataframe[constants.CHANNEL].iloc[0]
    first_channel_df = adstock_decay_dataframe[
        adstock_decay_dataframe[constants.CHANNEL] == first_channel
    ]

    self.assertAllClose(
        list(first_channel_df[constants.CI_HI])[:5],
        test_utils.ADSTOCK_DECAY_CI_HI,
        atol=1e-3,
    )

    self.assertAllClose(
        list(first_channel_df[constants.CI_LO])[:5],
        test_utils.ADSTOCK_DECAY_CI_LO,
        atol=1e-3,
    )

    self.assertAllClose(
        list(first_channel_df[constants.MEAN])[:5],
        test_utils.ADSTOCK_DECAY_MEAN,
        atol=1e-3,
    )

  def test_adstock_decay_time_unit_integer_indication_correct(self):
    adstock_decay_dataframe = self.analyzer_media_and_rf.adstock_decay(
        confidence_level=0.9
    )
    is_true_df = adstock_decay_dataframe[
        adstock_decay_dataframe[constants.IS_INT_TIME_UNIT]
    ]
    for i in range(len(is_true_df[constants.TIME_UNITS])):
      self.assertEqual(
          list(is_true_df[constants.TIME_UNITS])[i],
          int(list(is_true_df[constants.TIME_UNITS])[i]),
      )


class AnalyzerMediaOnlyTest(tf.test.TestCase, parameterized.TestCase):

  @classmethod
  def setUpClass(cls):
    super(AnalyzerMediaOnlyTest, cls).setUpClass()

    cls.input_data_media_only = (
        data_test_utils.sample_input_data_non_revenue_revenue_per_kpi(
            n_geos=_N_GEOS,
            n_times=_N_TIMES,
            n_media_times=_N_MEDIA_TIMES,
            n_controls=_N_CONTROLS,
            n_media_channels=_N_MEDIA_CHANNELS,
            seed=0,
        )
    )
    model_spec = spec.ModelSpec(max_lag=15)
    cls.meridian_media_only = model.Meridian(
        input_data=cls.input_data_media_only, model_spec=model_spec
    )
    cls.analyzer_media_only = analyzer.Analyzer(cls.meridian_media_only)

    cls.inference_data_media_only = _build_inference_data(
        _TEST_SAMPLE_PRIOR_MEDIA_ONLY_PATH,
        _TEST_SAMPLE_POSTERIOR_MEDIA_ONLY_PATH,
    )

    cls.enter_context(
        mock.patch.object(
            model.Meridian,
            "inference_data",
            new=property(lambda unused_self: cls.inference_data_media_only),
        )
    )

  def test_filter_and_aggregate_geos_and_times_incorrect_tensor_shape(self):
    with self.assertRaisesWithLiteralMatch(
        ValueError,
        "The tensor must have shape [..., n_geos, n_times, n_channels] or"
        " [..., n_geos, n_times].",
    ):
      self.analyzer_media_only.filter_and_aggregate_geos_and_times(
          tf.convert_to_tensor(self.input_data_media_only.population),
      )

  def test_filter_and_aggregate_geos_and_times_incorrect_geos(self):
    with self.assertRaisesWithLiteralMatch(
        ValueError,
        "`selected_geos` must match the geo dimension names from "
        "meridian.InputData.",
    ):
      self.analyzer_media_only.filter_and_aggregate_geos_and_times(
          tf.convert_to_tensor(self.input_data_media_only.media_spend),
          selected_geos=["random_geo"],
      )

  def test_filter_and_aggregate_geos_and_times_incorrect_times(self):
    with self.assertRaisesWithLiteralMatch(
        ValueError,
        "`selected_times` must match the time dimension names from "
        "meridian.InputData.",
    ):
      self.analyzer_media_only.filter_and_aggregate_geos_and_times(
          tf.convert_to_tensor(self.input_data_media_only.media_spend),
          selected_times=["random_time"],
      )

  @parameterized.product(
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
  )
  def test_filter_and_aggregate_geos_and_times_returns_correct_shape(
      self,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
      aggregate_geos: bool,
      aggregate_times: bool,
  ):
    tensor = tf.convert_to_tensor(self.input_data_media_only.media_spend)
    modified_tensor = (
        self.analyzer_media_only.filter_and_aggregate_geos_and_times(
            tensor,
            selected_geos=selected_geos,
            selected_times=selected_times,
            aggregate_geos=aggregate_geos,
            aggregate_times=aggregate_times,
        )
    )
    expected_shape = ()
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_MEDIA_CHANNELS,)
    self.assertEqual(modified_tensor.shape, expected_shape)

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      geos_to_include=[None, ["geo_1", "geo_3"]],
      times_to_include=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_expected_impact_media_only_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      geos_to_include: Sequence[str] | None,
      times_to_include: Sequence[str] | None,
  ):
    impact = self.analyzer_media_only.expected_impact(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=geos_to_include,
        selected_times=times_to_include,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(geos_to_include),) if geos_to_include is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(times_to_include),)
          if times_to_include is not None
          else (_N_TIMES,)
      )
    self.assertEqual(impact.shape, expected_shape)

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_incremental_impact_media_only_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    impact = self.analyzer_media_only.incremental_impact(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_MEDIA_CHANNELS,)
    self.assertEqual(impact.shape, expected_shape)

  # The purpose of this test is to prevent accidental logic change.
  @parameterized.named_parameters(
      dict(
          testcase_name="use_prior",
          use_posterior=False,
          expected_impact=test_utils.INC_IMPACT_MEDIA_ONLY_USE_PRIOR,
      ),
      dict(
          testcase_name="use_posterior",
          use_posterior=True,
          expected_impact=test_utils.INC_IMPACT_MEDIA_ONLY_USE_POSTERIOR,
      ),
  )
  def test_incremental_impact_media_only(
      self,
      use_posterior: bool,
      expected_impact: tuple[float, ...],
  ):
    impact = self.analyzer_media_only.incremental_impact(
        use_posterior=use_posterior,
    )
    self.assertAllClose(
        impact,
        tf.convert_to_tensor(expected_impact),
        rtol=1e-3,
        atol=1e-3,
    )

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
      by_reach=[False, True],
  )
  def test_marginal_roi_media_only_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
      by_reach: bool,
  ):
    mroi = self.analyzer_media_only.marginal_roi(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
        by_reach=by_reach,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_MEDIA_CHANNELS,)
    self.assertEqual(mroi.shape, expected_shape)

  # The purpose of this test is to prevent accidental logic change.
  @parameterized.named_parameters(
      dict(
          testcase_name="use_prior",
          use_posterior=False,
          by_reach=False,
          expected_mroi=test_utils.MROI_MEDIA_ONLY_USE_PRIOR,
      ),
      dict(
          testcase_name="use_prior_by_reach",
          use_posterior=False,
          by_reach=True,
          expected_mroi=test_utils.MROI_MEDIA_ONLY_USE_PRIOR,
      ),
      dict(
          testcase_name="use_posterior",
          use_posterior=True,
          by_reach=False,
          expected_mroi=test_utils.MROI_MEDIA_ONLY_USE_POSTERIOR,
      ),
      dict(
          testcase_name="use_posterior_by_reach",
          use_posterior=True,
          by_reach=True,
          expected_mroi=test_utils.MROI_MEDIA_ONLY_USE_POSTERIOR,
      ),
  )
  def test_marginal_roi_media_only(
      self, use_posterior: bool, by_reach: bool, expected_mroi: np.ndarray
  ):
    mroi = self.analyzer_media_only.marginal_roi(
        by_reach=by_reach,
        use_posterior=use_posterior,
    )
    self.assertAllClose(
        mroi,
        tf.convert_to_tensor(expected_mroi),
        rtol=1e-3,
        atol=1e-3,
    )

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_roi_media_only_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    roi = self.analyzer_media_only.roi(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_MEDIA_CHANNELS,)
    self.assertEqual(roi.shape, expected_shape)

  def test_roi_media_only_default_returns_correct_value(self):
    roi = self.analyzer_media_only.roi()
    total_spend = self.analyzer_media_only.filter_and_aggregate_geos_and_times(
        self.meridian_media_only.media_tensors.media_spend
    )
    expected_roi = self.analyzer_media_only.incremental_impact() / total_spend
    self.assertAllClose(expected_roi, roi)

  def test_roi_zero_media_returns_zero(self):
    new_media = tf.zeros_like(
        self.meridian_media_only.media_tensors.media, dtype=tf.float32
    )
    roi = self.analyzer_media_only.roi(new_media=new_media)
    self.assertAllClose(
        roi, tf.zeros((_N_CHAINS, _N_KEEP, _N_MEDIA_CHANNELS)), atol=2e-6
    )

  def test_optimal_frequency_data_media_only_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        "Must have at least one channel with reach and frequency data.",
    ):
      self.analyzer_media_only.optimal_freq()

  def test_r_hat_summary_media_only_correct(self):
    r_hat_summary = self.analyzer_media_only.r_hat_summary()
    self.assertEqual(r_hat_summary.shape, (13, 7))
    self.assertSetEqual(
        set(r_hat_summary.param),
        set(constants.COMMON_PARAMETER_NAMES + constants.MEDIA_PARAMETER_NAMES)
        - set([constants.SLOPE_M]),
    )

  def test_response_curves_returns_correct_data(self):
    response_curve_data = self.analyzer_media_only.response_curves()
    self.assertEqual(
        list(response_curve_data.coords.keys()),
        [constants.CHANNEL, constants.METRIC, constants.SPEND_MULTIPLIER],
    )
    self.assertEqual(
        list(response_curve_data.data_vars.keys()),
        [constants.SPEND, constants.INCREMENTAL_IMPACT],
    )
    response_curves_df = (
        response_curve_data[[constants.SPEND, constants.INCREMENTAL_IMPACT]]
        .to_dataframe()
        .reset_index()
        .pivot(
            index=[
                constants.CHANNEL,
                constants.SPEND,
                constants.SPEND_MULTIPLIER,
            ],
            columns=constants.METRIC,
            values=constants.INCREMENTAL_IMPACT,
        )
    ).reset_index()
    self.assertAllInSet(
        list(set(response_curves_df[constants.CHANNEL])),
        ["ch_0", "ch_2", "ch_1"],
    )
    self.assertEqual(max(response_curves_df[constants.SPEND_MULTIPLIER]), 2.0)
    for i, mean in enumerate(response_curves_df[constants.MEAN]):
      self.assertGreaterEqual(mean, response_curves_df[constants.CI_LO][i])
      self.assertLessEqual(mean, response_curves_df[constants.CI_HI][i])

  @parameterized.product(
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_media_summary_returns_correct_shapes(
      self,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    model_analyzer = self.analyzer_media_only
    num_channels = _N_MEDIA_CHANNELS

    media_summary = model_analyzer.media_summary_metrics(
        confidence_level=0.8,
        marginal_roi_by_reach=False,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_channel_shape = ()
    if not aggregate_geos:
      expected_channel_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_channel_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )

    # (ch_1, ch_2, ..., Total_media, [mean, ci_lo, ci_hi], [prior, posterior])
    expected_channel_shape += (num_channels + 1,)
    expected_shape = expected_channel_shape + (
        3,
        2,
    )
    self.assertEqual(media_summary.impressions.shape, expected_channel_shape)
    self.assertEqual(
        media_summary.pct_of_impressions.shape, expected_channel_shape
    )
    self.assertEqual(media_summary.spend.shape, expected_channel_shape)
    self.assertEqual(media_summary.pct_of_spend.shape, expected_channel_shape)
    self.assertEqual(media_summary.cpm.shape, expected_channel_shape)
    self.assertEqual(media_summary.incremental_impact.shape, expected_shape)
    self.assertEqual(media_summary.pct_of_contribution.shape, expected_shape)
    self.assertEqual(media_summary.roi.shape, expected_shape)
    self.assertEqual(media_summary.effectiveness.shape, expected_shape)
    self.assertEqual(media_summary.mroi.shape, expected_shape)


class AnalyzerRFOnlyTest(tf.test.TestCase, parameterized.TestCase):

  @classmethod
  def setUpClass(cls):
    super(AnalyzerRFOnlyTest, cls).setUpClass()

    cls.input_data_rf_only = (
        data_test_utils.sample_input_data_non_revenue_revenue_per_kpi(
            n_geos=_N_GEOS,
            n_times=_N_TIMES,
            n_media_times=_N_MEDIA_TIMES,
            n_controls=_N_CONTROLS,
            n_rf_channels=_N_RF_CHANNELS,
            seed=0,
        )
    )
    model_spec = spec.ModelSpec(max_lag=15)
    cls.meridian_rf_only = model.Meridian(
        input_data=cls.input_data_rf_only, model_spec=model_spec
    )

    cls.analyzer_rf_only = analyzer.Analyzer(cls.meridian_rf_only)

    cls.inference_data_rf_only = _build_inference_data(
        _TEST_SAMPLE_PRIOR_RF_ONLY_PATH,
        _TEST_SAMPLE_POSTERIOR_RF_ONLY_PATH,
    )

    cls.enter_context(
        mock.patch.object(
            model.Meridian,
            "inference_data",
            new=property(lambda unused_self: cls.inference_data_rf_only),
        )
    )

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      geos_to_include=[None, ["geo_1", "geo_3"]],
      times_to_include=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_expected_impact_rf_only_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      geos_to_include: Sequence[str] | None,
      times_to_include: Sequence[str] | None,
  ):
    impact = self.analyzer_rf_only.expected_impact(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=geos_to_include,
        selected_times=times_to_include,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(geos_to_include),) if geos_to_include is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(times_to_include),)
          if times_to_include is not None
          else (_N_TIMES,)
      )
    self.assertEqual(impact.shape, expected_shape)

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_incremental_impact_rf_only_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    impact = self.analyzer_rf_only.incremental_impact(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_RF_CHANNELS,)
    self.assertEqual(impact.shape, expected_shape)

  # The purpose of this test is to prevent accidental logic change.
  @parameterized.named_parameters(
      dict(
          testcase_name="use_prior",
          use_posterior=False,
          expected_impact=test_utils.INC_IMPACT_RF_ONLY_USE_PRIOR,
      ),
      dict(
          testcase_name="use_posterior",
          use_posterior=True,
          expected_impact=test_utils.INC_IMPACT_RF_ONLY_USE_POSTERIOR,
      ),
  )
  def test_incremental_impact_rf_only(
      self,
      use_posterior: bool,
      expected_impact: tuple[float, ...],
  ):
    impact = self.analyzer_rf_only.incremental_impact(
        use_posterior=use_posterior,
    )
    self.assertAllClose(
        impact,
        tf.convert_to_tensor(expected_impact),
        rtol=1e-3,
        atol=1e-3,
    )

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
      by_reach=[False, True],
  )
  def test_marginal_roi_rf_only_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
      by_reach: bool,
  ):
    mroi = self.analyzer_rf_only.marginal_roi(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
        by_reach=by_reach,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_RF_CHANNELS,)
    self.assertEqual(mroi.shape, expected_shape)

  # The purpose of this test is to prevent accidental logic change.
  @parameterized.named_parameters(
      dict(
          testcase_name="use_prior",
          use_posterior=False,
          by_reach=False,
          expected_mroi=test_utils.MROI_RF_ONLY_USE_PRIOR,
      ),
      dict(
          testcase_name="use_prior_by_reach",
          use_posterior=False,
          by_reach=True,
          expected_mroi=test_utils.MROI_RF_ONLY_USE_PRIOR_BY_REACH,
      ),
      dict(
          testcase_name="use_posterior",
          use_posterior=True,
          by_reach=False,
          expected_mroi=test_utils.MROI_RF_ONLY_USE_POSTERIOR,
      ),
      dict(
          testcase_name="use_posterior_by_reach",
          use_posterior=True,
          by_reach=True,
          expected_mroi=test_utils.MROI_RF_ONLY_USE_POSTERIOR_BY_REACH,
      ),
  )
  def test_marginal_roi_rf_only(
      self,
      use_posterior: bool,
      by_reach: bool,
      expected_mroi: tuple[float, ...],
  ):
    mroi = self.analyzer_rf_only.marginal_roi(
        by_reach=by_reach,
        use_posterior=use_posterior,
    )
    self.assertAllClose(
        mroi,
        tf.convert_to_tensor(expected_mroi),
        rtol=1e-3,
        atol=1e-3,
    )

  @parameterized.product(
      use_posterior=[False, True],
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_roi_rf_only_returns_correct_shape(
      self,
      use_posterior: bool,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    roi = self.analyzer_rf_only.roi(
        use_posterior=use_posterior,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_shape = (_N_CHAINS, _N_KEEP) if use_posterior else (1, _N_DRAWS)
    if not aggregate_geos:
      expected_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )
    expected_shape += (_N_RF_CHANNELS,)
    self.assertEqual(roi.shape, expected_shape)

  def test_roi_rf_only_default_returns_correct_value(self):
    roi = self.analyzer_rf_only.roi()
    total_spend = self.analyzer_rf_only.filter_and_aggregate_geos_and_times(
        self.meridian_rf_only.rf_tensors.rf_spend
    )
    expeted_roi = self.analyzer_rf_only.incremental_impact() / total_spend
    self.assertAllClose(expeted_roi, roi)

  def test_by_reach_returns_correct_values(self):
    mroi = self.analyzer_rf_only.marginal_roi(
        use_posterior=True,
        aggregate_geos=True,
        aggregate_times=True,
        selected_geos=None,
        selected_times=None,
        by_reach=True,
    )
    roi = self.analyzer_rf_only.roi(
        use_posterior=True,
        aggregate_geos=True,
        aggregate_times=True,
        selected_geos=None,
        selected_times=None,
    )
    self.assertAllClose(
        mroi,
        roi,
        atol=1e-2,
        rtol=1e-2,
    )

  def test_media_summary_warns_if_time_not_aggregated(self):
    with warnings.catch_warnings(record=True) as w:
      warnings.simplefilter("always")
      media_summary = self.analyzer_rf_only.media_summary_metrics(
          confidence_level=0.8,
          marginal_roi_by_reach=False,
          aggregate_geos=True,
          aggregate_times=False,
          selected_geos=None,
          selected_times=None,
      )
      self.assertLen(w, 1)
      self.assertTrue(issubclass(w[0].category, UserWarning))
      self.assertIn(
          "ROI, mROI, and Effectiveness are not reported because they do not"
          " have a clear interpretation by time period.",
          str(w[0].message),
      )
      self.assertTrue((media_summary.roi.isnull()).all())
      self.assertTrue((media_summary.mroi.isnull()).all())
      self.assertTrue((media_summary.effectiveness.isnull()).all())

  def test_optimal_frequency_data_rf_only_correct(self):
    actual = self.analyzer_rf_only.optimal_freq(
        freq_grid=[1.0, 2.0, 3.0],
        confidence_level=0.9,
        use_posterior=True,
    )
    expected = xr.Dataset(
        coords={
            constants.FREQUENCY: ([constants.FREQUENCY], [1.0, 2.0, 3.0]),
            constants.RF_CHANNEL: (
                [constants.RF_CHANNEL],
                ["rf_ch_0", "rf_ch_1"],
            ),
            constants.METRIC: (
                [constants.METRIC],
                [constants.MEAN, constants.CI_LO, constants.CI_HI],
            ),
        },
        data_vars={
            constants.ROI: (
                [constants.FREQUENCY, constants.RF_CHANNEL, constants.METRIC],
                [  # rf_ch_0.           # rf_ch_1
                    [[3.34, 0.20, 10.17], [7.15, 0.42, 13.10]],  # freq=1.0
                    [[4.76, 0.66, 10.79], [4.83, 1.13, 7.86]],  # freq=2.0
                    [[4.87, 1.17, 9.60], [3.72, 1.47, 5.71]],  # freq=3.0
                ],
            ),
            constants.OPTIMAL_FREQUENCY: ([constants.RF_CHANNEL], [3.0, 1.0]),
            constants.OPTIMIZED_INCREMENTAL_IMPACT: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[915.6, 380.9, 1647.8], [683.3, 305.7, 1041.2]],
            ),
            constants.OPTIMIZED_EFFECTIVENESS: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[0.000416, 0.000173, 0.000749], [0.000930, 0.000416, 0.00141]],
            ),
            constants.OPTIMIZED_PCT_OF_CONTRIBUTION: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[0.01113, 0.00463, 0.02003], [0.00830, 0.00371, 0.01266]],
            ),
            constants.OPTIMIZED_ROI: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[3.364, 1.399, 6.054], [2.373, 1.062, 3.616]],
            ),
            constants.OPTIMIZED_MROI_BY_REACH: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[3.365, 1.401, 6.051], [2.373, 1.062, 3.620]],
            ),
            constants.OPTIMIZED_MROI_BY_FREQUENCY: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[1.685, 1.187, 2.851], [0.566, 0.288, 0.714]],
            ),
        },
        attrs={
            constants.CONFIDENCE_LEVEL: 0.9,
            "use_posterior": True,
        },
    )

    xr.testing.assert_allclose(actual, expected, atol=0.1)
    xr.testing.assert_allclose(actual.frequency, expected.frequency, atol=0.1)
    xr.testing.assert_allclose(actual.rf_channel, expected.rf_channel, atol=0.1)
    xr.testing.assert_allclose(actual.metric, expected.metric, atol=0.1)
    xr.testing.assert_allclose(actual.roi, expected.roi, atol=0.1)
    xr.testing.assert_allclose(
        actual.optimal_frequency, expected.optimal_frequency, atol=0.1
    )
    xr.testing.assert_allclose(
        actual.optimized_incremental_impact,
        expected.optimized_incremental_impact,
        atol=0.1,
    )
    xr.testing.assert_allclose(
        actual.optimized_effectiveness,
        expected.optimized_effectiveness,
        atol=0.00001,
    )
    xr.testing.assert_allclose(
        actual.optimized_pct_of_contribution,
        expected.optimized_pct_of_contribution,
        atol=0.0001,
    )
    xr.testing.assert_allclose(
        actual.optimized_roi,
        expected.optimized_roi,
        atol=0.01,
    )
    xr.testing.assert_allclose(
        actual.optimized_mroi_by_reach,
        expected.optimized_mroi_by_reach,
        atol=0.01,
    )
    xr.testing.assert_allclose(
        actual.optimized_mroi_by_frequency,
        expected.optimized_mroi_by_frequency,
        atol=0.01,
    )
    self.assertEqual(actual.confidence_level, 0.9)
    self.assertEqual(actual.use_posterior, expected.use_posterior)

  def test_optimal_frequency_freq_grid(self):
    max_freq = np.max(
        np.array(self.analyzer_rf_only._meridian.rf_tensors.frequency)
    )
    freq_grid = list(np.arange(1, max_freq, 0.1))
    roi = np.zeros(
        (len(freq_grid), self.analyzer_rf_only._meridian.n_rf_channels, 3)
    )
    for i, freq in enumerate(freq_grid):
      new_frequency = (
          tf.ones_like(self.analyzer_rf_only._meridian.rf_tensors.frequency)
          * freq
      )
      new_reach = (
          self.analyzer_rf_only._meridian.rf_tensors.frequency
          * self.analyzer_rf_only._meridian.rf_tensors.reach
          / new_frequency
      )
      dim_kwargs = {
          "selected_geos": None,
          "selected_times": None,
          "aggregate_geos": True,
          "aggregate_times": True,
      }
      roi_temp = self.analyzer_rf_only.roi(
          new_reach=new_reach,
          new_frequency=new_frequency,
          use_posterior=True,
          **dim_kwargs,
      )[..., -self.analyzer_rf_only._meridian.n_rf_channels :]
      roi[i, :, 0] = np.mean(roi_temp, (0, 1))
      roi[i, :, 1] = np.quantile(roi_temp, (1 - 0.9) / 2, (0, 1))
      roi[i, :, 2] = np.quantile(roi_temp, (1 + 0.9) / 2, (0, 1))

      self.assertAllEqual(roi[i, :, 0], np.mean(roi_temp, (0, 1)))
      self.assertAllEqual(
          roi[i, :, 1], np.quantile(roi_temp, (1 - 0.9) / 2, (0, 1))
      )
      self.assertAllEqual(
          roi[i, :, 2], np.quantile(roi_temp, (1 + 0.9) / 2, (0, 1))
      )

  def test_r_hat_summary_rf_only_correct(self):
    r_hat_summary = self.analyzer_rf_only.r_hat_summary()
    self.assertEqual(r_hat_summary.shape, (14, 7))
    self.assertSetEqual(
        set(r_hat_summary.param),
        set(constants.COMMON_PARAMETER_NAMES + constants.RF_PARAMETER_NAMES),
    )

  def test_response_curves_check_only_rf_channels_returns_correct_spend(self):
    response_curve_data = self.analyzer_rf_only.response_curves(by_reach=False)
    response_data_spend = response_curve_data.spend.values

    media_summary_spend = self.analyzer_rf_only.media_summary_metrics(
        confidence_level=0.9,
        marginal_roi_by_reach=False,
    ).spend[:-1]
    self.assertAllEqual(
        media_summary_spend * 2,
        response_data_spend[-1],
    )

  @parameterized.product(
      aggregate_geos=[False, True],
      aggregate_times=[False, True],
      selected_geos=[None, ["geo_1", "geo_3"]],
      selected_times=[None, ["2021-04-19", "2021-09-13", "2021-12-13"]],
  )
  def test_media_summary_returns_correct_shapes(
      self,
      aggregate_geos: bool,
      aggregate_times: bool,
      selected_geos: Sequence[str] | None,
      selected_times: Sequence[str] | None,
  ):
    model_analyzer = self.analyzer_rf_only
    num_channels = _N_RF_CHANNELS

    media_summary = model_analyzer.media_summary_metrics(
        confidence_level=0.8,
        marginal_roi_by_reach=False,
        aggregate_geos=aggregate_geos,
        aggregate_times=aggregate_times,
        selected_geos=selected_geos,
        selected_times=selected_times,
    )
    expected_channel_shape = ()
    if not aggregate_geos:
      expected_channel_shape += (
          (len(selected_geos),) if selected_geos is not None else (_N_GEOS,)
      )
    if not aggregate_times:
      expected_channel_shape += (
          (len(selected_times),) if selected_times is not None else (_N_TIMES,)
      )

    # (ch_1, ch_2, ..., Total_media, [mean, ci_lo, ci_hi], [prior, posterior])
    expected_channel_shape += (num_channels + 1,)
    expected_shape = expected_channel_shape + (
        3,
        2,
    )
    self.assertEqual(media_summary.impressions.shape, expected_channel_shape)
    self.assertEqual(
        media_summary.pct_of_impressions.shape, expected_channel_shape
    )
    self.assertEqual(media_summary.spend.shape, expected_channel_shape)
    self.assertEqual(media_summary.pct_of_spend.shape, expected_channel_shape)
    self.assertEqual(media_summary.cpm.shape, expected_channel_shape)
    self.assertEqual(media_summary.incremental_impact.shape, expected_shape)
    self.assertEqual(media_summary.pct_of_contribution.shape, expected_shape)
    self.assertEqual(media_summary.roi.shape, expected_shape)
    self.assertEqual(media_summary.effectiveness.shape, expected_shape)
    self.assertEqual(media_summary.mroi.shape, expected_shape)


class AnalyzerKpiTest(tf.test.TestCase, parameterized.TestCase):

  @classmethod
  def setUpClass(cls):
    super(AnalyzerKpiTest, cls).setUpClass()

    input_data = (
        data_test_utils.sample_input_data_non_revenue_no_revenue_per_kpi(
            n_geos=_N_GEOS,
            n_times=_N_TIMES,
            n_media_times=_N_MEDIA_TIMES,
            n_controls=_N_CONTROLS,
            n_media_channels=_N_MEDIA_CHANNELS,
            n_rf_channels=_N_RF_CHANNELS,
            seed=0,
        )
    )
    cpik_prior = tfp.distributions.LogNormal(0.5, 0.5)
    roi_prior = tfp.distributions.TransformedDistribution(
        cpik_prior, tfp.bijectors.Reciprocal()
    )
    custom_prior = prior_distribution.PriorDistribution(
        roi_m=roi_prior, roi_rf=roi_prior
    )
    model_spec = spec.ModelSpec(prior=custom_prior)
    cls.meridian_kpi = model.Meridian(
        input_data=input_data, model_spec=model_spec
    )
    cls.analyzer_kpi = analyzer.Analyzer(cls.meridian_kpi)
    inference_data = _build_inference_data(
        _TEST_SAMPLE_PRIOR_MEDIA_AND_RF_PATH,
        _TEST_SAMPLE_POSTERIOR_MEDIA_AND_RF_PATH,
    )
    cls.enter_context(
        mock.patch.object(
            model.Meridian,
            "inference_data",
            new=property(lambda unused_self: inference_data),
        )
    )

  def test_use_kpi_expected_vs_actual_data_expected_impact_correct_usage(self):
    mock_expected_impact = self.enter_context(
        mock.patch.object(
            self.analyzer_kpi,
            "expected_impact",
            return_value=tf.ones((
                _N_CHAINS,
                _N_DRAWS,
                _N_GEOS,
                _N_TIMES,
            )),
        )
    )
    self.analyzer_kpi.expected_vs_actual_data()
    _, mock_kwargs = mock_expected_impact.call_args
    self.assertEqual(mock_kwargs["use_kpi"], True)

  def test_use_kpi_no_revenue_per_kpi_correct_usage_expected_vs_actual(self):
    expected_vs_actual = self.analyzer_kpi.expected_vs_actual_data(
        confidence_level=0.9
    )
    self.assertAllClose(
        list(expected_vs_actual.data_vars[constants.ACTUAL].values[:5]),
        list(self.meridian_kpi.kpi[:5]),
        atol=1e-3,
    )

  def test_use_kpi_no_revenue_per_kpi_correct_usage_media_summary_metrics(self):
    media_summary = self.analyzer_kpi.media_summary_metrics(
        confidence_level=0.9,
        marginal_roi_by_reach=False,
        aggregate_geos=True,
        aggregate_times=True,
        selected_geos=None,
        selected_times=None,
    )
    self.assertEqual(
        list(media_summary.data_vars.keys()),
        [
            constants.IMPRESSIONS,
            constants.PCT_OF_IMPRESSIONS,
            constants.SPEND,
            constants.PCT_OF_SPEND,
            constants.CPM,
            constants.INCREMENTAL_IMPACT,
            constants.PCT_OF_CONTRIBUTION,
            constants.EFFECTIVENESS,
            constants.CPIK,
        ],
    )
    self.assertAllClose(
        media_summary.cpik, test_utils.SAMPLE_CPIK, atol=1e-3, rtol=1e-3
    )

  def test_media_summary_metrics_no_time_period_warning(self):
    with warnings.catch_warnings(record=True) as w:
      self.analyzer_kpi.media_summary_metrics(
          confidence_level=0.8,
          marginal_roi_by_reach=False,
          aggregate_geos=True,
          aggregate_times=False,
          selected_geos=None,
          selected_times=None,
      )
      self.assertLen(w, 1)
      self.assertTrue(issubclass(w[0].category, UserWarning))
      self.assertIn(
          "Effectiveness and CPIK are not reported because they do not have a"
          " clear interpretation by time period.",
          str(w[0].message),
      )

  def test_use_kpi_no_revenue_per_kpi_correct_usage_response_curves(self):
    mock_incremental_impact = self.enter_context(
        mock.patch.object(
            self.analyzer_kpi,
            "incremental_impact",
            return_value=tf.ones((
                _N_CHAINS,
                _N_DRAWS,
                _N_MEDIA_CHANNELS + _N_RF_CHANNELS,
            )),
        )
    )
    self.analyzer_kpi.response_curves()
    _, mock_kwargs = mock_incremental_impact.call_args
    self.assertEqual(mock_kwargs["use_kpi"], True)

  def test_use_kpi_no_revenue_per_kpi_correct_usage_expected_impact(self):
    with self.assertRaisesWithLiteralMatch(
        ValueError,
        "`use_kpi` must be True when `revenue_per_kpi` is not defined.",
    ):
      self.analyzer_kpi.expected_impact()

  def test_optimal_frequency_uses_cpik_if_no_revenue_per_kpi(self):
    with mock.patch.object(
        self.analyzer_kpi, "cpik", autospec=True
    ) as mock_cpik:
      mock_cpik.return_value = tf.ones(
          [_N_DRAWS, _N_DRAWS, _N_MEDIA_CHANNELS + _N_RF_CHANNELS]
      )
      ds = self.analyzer_kpi.optimal_freq()
      mock_cpik.assert_called()
      self.assertIn(constants.CPIK, list(ds.data_vars.keys()))

  def test_optimal_frequency_data_with_cpik_correct(self):
    actual = self.analyzer_kpi.optimal_freq(
        freq_grid=[1.0, 2.0, 3.0],
        confidence_level=0.9,
        use_posterior=True,
    )
    expected = xr.Dataset(
        coords={
            constants.FREQUENCY: ([constants.FREQUENCY], [1.0, 2.0, 3.0]),
            constants.RF_CHANNEL: (
                [constants.RF_CHANNEL],
                ["rf_ch_0", "rf_ch_1"],
            ),
            constants.METRIC: (
                [constants.METRIC],
                [constants.MEAN, constants.CI_LO, constants.CI_HI],
            ),
        },
        data_vars={
            constants.CPIK: (
                [constants.FREQUENCY, constants.RF_CHANNEL, constants.METRIC],
                [  # rf_ch_0.             # rf_ch_1
                    [[1.166, 0.417, 1.959], [1.041, 0.266, 1.819]],  # freq=1.0
                    [[1.984, 0.790, 3.214], [1.650, 0.493, 2.811]],  # freq=2.0
                    [[2.741, 1.150, 4.361], [2.218, 0.724, 3.716]],  # freq=3.0
                ],
            ),
            constants.OPTIMAL_FREQUENCY: ([constants.RF_CHANNEL], [1.0, 1.0]),
            constants.OPTIMIZED_INCREMENTAL_IMPACT: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[99.699, 41.435, 157.754], [145.965, 51.569, 243.965]],
            ),
            constants.OPTIMIZED_EFFECTIVENESS: (
                [constants.RF_CHANNEL, constants.METRIC],
                [
                    [1.360e-04, 5.655e-05, 2.153e-04],
                    [1.986e-04, 7.019e-05, 3.320e-04],
                ],
            ),
            constants.OPTIMIZED_PCT_OF_CONTRIBUTION: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[1.43945, 0.59824, 2.27765], [2.10744, 0.74456, 3.52236]],
            ),
            constants.OPTIMIZED_CPIK: (
                [constants.RF_CHANNEL, constants.METRIC],
                [[4.123, 1.725, 6.568], [3.378, 1.179, 5.582]],
            ),
        },
        attrs={
            constants.CONFIDENCE_LEVEL: 0.9,
            "use_posterior": True,
        },
    )

    xr.testing.assert_allclose(actual, expected, atol=0.1)
    xr.testing.assert_allclose(actual.frequency, expected.frequency, atol=0.1)
    xr.testing.assert_allclose(actual.rf_channel, expected.rf_channel, atol=0.1)
    xr.testing.assert_allclose(actual.metric, expected.metric, atol=0.1)
    xr.testing.assert_allclose(actual.cpik, expected.cpik, atol=0.1)
    xr.testing.assert_allclose(
        actual.optimal_frequency, expected.optimal_frequency, atol=0.1
    )
    xr.testing.assert_allclose(
        actual.optimized_incremental_impact,
        expected.optimized_incremental_impact,
        atol=0.1,
    )
    xr.testing.assert_allclose(
        actual.optimized_effectiveness,
        expected.optimized_effectiveness,
        atol=0.00001,
    )
    xr.testing.assert_allclose(
        actual.optimized_pct_of_contribution,
        expected.optimized_pct_of_contribution,
        atol=0.0001,
    )
    xr.testing.assert_allclose(
        actual.optimized_cpik,
        expected.optimized_cpik,
        atol=0.01,
    )
    self.assertEqual(actual.confidence_level, 0.9)
    self.assertEqual(actual.use_posterior, expected.use_posterior)

  def test_optimal_frequency_min_cpik_at_opt_freq(self):
    ds = self.analyzer_kpi.optimal_freq()
    df = ds.to_dataframe()
    cpik_at_opt_freq = list(
        df.query(
            'frequency == optimal_frequency & metric == "mean"'
        ).cpik.values
    )
    min_mean_cpik = list(ds.cpik[:, :, 0].min(axis=0).values)
    cpik_at_opt_freq.sort()
    min_mean_cpik.sort()
    self.assertListEqual(cpik_at_opt_freq, min_mean_cpik)


class AnalyzerNotFittedTest(absltest.TestCase):

  def test_r_hat_summary_media_and_rf_pre_fitting_raises_exception(self):
    not_fitted_mmm = mock.create_autospec(model.Meridian, instance=True)
    type(not_fitted_mmm).inference_data = mock.PropertyMock(
        return_value=az.InferenceData()
    )
    not_fitted_analyzer = analyzer.Analyzer(not_fitted_mmm)
    with self.assertRaisesWithLiteralMatch(
        model.NotFittedModelError,
        "sample_posterior() must be called prior to calling this method.",
    ):
      not_fitted_analyzer.r_hat_summary()


if __name__ == "__main__":
  absltest.main()
