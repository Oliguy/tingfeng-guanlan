from guanlan_data.config import current
from guanlan_data.repositories.industry_index.config import result_path; from guanlan_data.repositories.industry_index.config import input_paths
def root():return current().path('collection_root')
def catalog_path():return root()/'catalog.sqlite'
def results_path():return root()/'results.sqlite'
def support_path():return current().path('support_db')
