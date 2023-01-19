"""Model for the Access Uri Combination."""
# pylint: disable=no-name-in-module
from typing import List
from pydantic import BaseModel


# pragma pylint: disable=too-few-public-methods
class TesUriList(BaseModel):
    """Combination of the tes_uri and total distance."""

    tes_uri: str
    total_distance: int = None


class AccessUriCombination(BaseModel):
    """Combination of input_uri of the TES task and the TES instances."""

    input_uri: List[str]
    tes_uri_list: List[TesUriList]

    def convert_tes_uri_list_to_dict(self):
        """Convert the list of type TESUriList to list of dictionary."""
        converted_list = []
        for combo in self.tes_uri_list:
            converted_list.append(combo.__dict__)
        return converted_list

    def convert_combination_to_dict(self):
        """Convert the AccessUriCombination object to dictionary."""
        tes_uri_list = self.convert_tes_uri_list_to_dict()
        return {"input_uri": self.input_uri, "tes_uri_list": tes_uri_list}
