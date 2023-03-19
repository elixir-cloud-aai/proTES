"""Unit test for distance-based middleware."""
import unittest
from unittest.mock import patch

from flask import Flask
from foca.models.config import Config, MongoConfig
import mongomock
import pytest

import pro_tes
from pro_tes.exceptions import InputUriError, TesUriError
from pro_tes.middleware.task_distribution.distance import (
    calculate_distance,
    get_uri_combination,
    ip_combination,
    ip_distance,
    rank_tes_instances,
    task_distribution
)
from tests.unitTest.pro_tes.middleware.mock_data import (
    CONTROLLER_CONFIG,
    MONGO_CONFIG,
    SERVICE_INFO_CONFIG,
    TES_CONFIG,
    expected_access_uri_combination,
    expected_distances,
    expected_ips,
    final_access_uri_combination,
    invalid_input_uri,
    invalid_tes_uri,
    ip_distances_res,
    ips_all,
    invalid_ips,
    ips_not_unique,
    ips_unique,
    invalid_ips_unique,
    mock_input_uri,
    mock_rank_tes_instances,
    mock_tes_uri,
    invalid_ip_distances_res,
)


class TestDistanceBasedTaskDistribution(unittest.TestCase):
    """Test distance-based task distribution logic."""

    app = Flask(__name__)

    def setUp(self):
        """Set up the test environment."""
        self.app.config.foca = Config(
            db=MongoConfig(**MONGO_CONFIG),
            controllers=CONTROLLER_CONFIG,
            tes=TES_CONFIG,
            serviceInfo=SERVICE_INFO_CONFIG,
        )
        self.app.config.foca.db.dbs["taskStore"].collections[
            "tasks"
        ].client = mongomock.MongoClient().db.collection
        self.tes_uri = mock_tes_uri
        self.input_uri = mock_input_uri
        self.ips_unique = ips_unique
        self.ips_all = ips_all
        self.final_access_uri_combination = final_access_uri_combination

    @pytest.mark.run(order=1)
    def test_get_uri_combination(self):
        """Test get_uri_combination.

        Ensure that `get_uri_combination` returns the expected access URI
        combination based on the mock TES URI and input URI provided.
        """
        self.setUp()
        with self.app.app_context():
            access_uri_combination = get_uri_combination(
                self.input_uri, self.tes_uri
            )
            assert access_uri_combination == expected_access_uri_combination

    def test_ip_combination(self):
        """Test ip_combination.

        Ensure that `ip_combination` returns the expected IP combinations based
        on the mock TES URI and mock input URI provided.
        """
        self.setUp()
        with self.app.app_context():
            ips = ip_combination(self.input_uri, self.tes_uri)
            assert ips == expected_ips

    def test_ip_combination_invalid_input_uri(self):
        """Test ip_combination with invalid input URI.

        Ensures that an InputUriError is raised when an invalid input URI is
        passed to ip_combination.
        """
        with pytest.raises(InputUriError):
            ip_combination(invalid_input_uri, mock_input_uri)

    def test_ip_combination_invalid_tes_uri(self):
        """Test ip_combination with invalid TES URI.

        Raises: TesUriError
        """
        with pytest.raises(TesUriError):
            ip_combination(mock_tes_uri, invalid_tes_uri)

    @pytest.mark.run(order=3)
    def test_ip_distance(self):
        """Test ip_distance.

        Ensure that `ip_distance` returns the expected IP distances based on
        the mock IP combinations provided.
        """
        self.setUp()
        with self.app.app_context():
            result = ip_distance(*self.ips_all)
            assert result == ip_distances_res

    def test_ip_distance_invalid_ip(self):
        """Test ip_distance with invalid IP."""
        ip_distance(*invalid_ips)

    def test_ip_distance_empty_ips(self):
        """Test ip_distance with empty IP.

        This test checks that calling the ip_distance function with no IP
         addresses as arguments raises a ValueError exception.
        """
        with pytest.raises(ValueError):
            ip_distance()

    def test_calculate_distance(self) -> None:
        """Test calculate_distance.

        Ensure that `calculate_distance` returns the expected distances based
        on the mock IP combinations and TES URI provided. The `ip_distance`
        function is mocked to return pre-defined distances.
        """
        self.setUp()
        with self.app.app_context():
            with patch.object(
                    pro_tes.middleware.task_distribution.distance,
                    "ip_distance",
            ) as ip_distance_mock:
                ip_distance_mock.return_value = ip_distances_res
                result = calculate_distance(self.ips_unique, self.tes_uri)
                assert result == expected_distances

    def test_calculate_distance_key_error(self):
        """Test calculate_distance with KeyError.

        Ensures that the function raises a KeyError when the `ip_distance`
        function returns a dictionary that is missing expected keys.
        """
        with pytest.raises(KeyError):
            with patch.object(
                    pro_tes.middleware.task_distribution.distance,
                    "ip_distance",
            ) as ip_distance_mock:
                ip_distance_mock.return_value = invalid_ip_distances_res
                calculate_distance(self.ips_unique, self.tes_uri)

    def test_calculate_distance_invalid_ips_unique(self):
        """Test calculate_distance function with invalid IPs."""
        with pytest.raises(ValueError):
            with patch.object(
                    pro_tes.middleware.task_distribution.distance,
                    "ip_distance",
            ) as ip_distance_mock:
                ip_distance_mock.side_effect = ValueError
                calculate_distance(invalid_ips_unique, self.tes_uri)

    def test_calculate_distance_no_unique_ips(self):
        """Test calculate_distance function when no unique IPs are found."""
        calculate_distance(ips_not_unique, self.tes_uri)

    def test_ranked_tes_instances(self):
        """Test rank_tes_instances.

        Ensure that `rank_tes_instances` returns the expected ranked list of
        TES instances based on the mock final access URI combination provided.
        """
        self.setUp()
        with self.app.app_context():
            result = rank_tes_instances(final_access_uri_combination)
            assert result == mock_rank_tes_instances

    def test_distance_based_task_distribution(self) -> None:
        """Test distance-based task distribution with valid input URI.

        Ensures that the distance-based task distribution function returns the
        expected result when given a valid input URI. This test case verifies
        that the function correctly computes IP distances and ranks the TES
        instances based on their proximity to the input URI.

        Test strategy:
        - Mock the get_uri_combination, calculate_distance, and ip_combination
          functions to return the expected values for a given input URI.
        - Call the distance-based task distribution function with input URI.
        - Verify that the function returns the expected result.
        """
        self.setUp()

        with self.app.app_context():
            with patch.object(
                    pro_tes.middleware.task_distribution.distance,
                    "get_uri_combination",
            ) as get_uri_combination_mock, patch.object(
                pro_tes.middleware.task_distribution.distance,
                "calculate_distance",
            ) as calculate_distance_mock, patch.object(
                pro_tes.middleware.task_distribution.distance, "ip_combination"
            ) as ip_combination_mock:
                get_uri_combination_mock.return_value = (
                    expected_access_uri_combination
                )
                calculate_distance_mock.return_value = expected_distances
                ip_combination_mock.return_value = expected_ips
                result = task_distribution(self.input_uri)
                assert result == mock_rank_tes_instances

    def test_distance_based_task_distribution_empty_input_uri(self):
        """
        Test distance-based task distribution with empty input URI list.

        Ensures that a ValueError is raised when an empty list is passed to the
        distance-based task distribution function. This test case verifies that
        the function handles empty input gracefully and does not raise any
        unexpected errors.

        Test strategy:
        - Pass an empty list to the distance-based task distribution function.
        - Verify that a ValueError is raised.
        """
        with pytest.raises(ValueError):
            task_distribution([])
