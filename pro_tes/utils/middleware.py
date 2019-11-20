"""Utility functions to inject and process results from the middleware TEStribute"""
import logging
from typing import Dict, List
import addict

from TEStribute import rank_services


class injectTEStribute:
    def __init__ (
        self,
        drs_uris,
        task_id_tes,
        task,
        tes_uris,
    )-> None:

        self.task_id=task_id_tes
        
        #TODO: raise errors if no inputs or outputs
        self.drs_input_ids = [i['name'] for i in task['inputs']]
        self.drs_output_ids = [i['name'] for i in task['inputs']]        
        
        #TODO: replace when needed
        try:
            self.resource_requirements=task['resources']['execution_time_sec']
        except KeyError:
            task['resources']['execution_time_sec']=3600
            self.resource_requirements=task['resources']
        
        self.drs_uris=drs_uris
        self.tes_uris=tes_uris

        
    def run_ranking(self)->Dict:
        rank = rank_services(
            drs_uris=self.drs_uris,
            tes_uris=self.tes_uris,
            resource_requirements=self.resource_requirements,
        )
        
        return rank
