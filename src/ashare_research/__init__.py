"""A 股研究数据平台。"""

from .datasets.catalog import DatasetCatalog, default_dataset_specs
from .connectors import AkshareConnector, ConnectorRegistry, TushareConnector
from .evidence import EvidenceRecord, EvidenceStore
from .features import FeatureBuilder, FeatureRegistry, FeatureStore, default_feature_specs
from .knowledge import KnowledgeStore
from .marts.publisher import MartPublisher
from .marts.reader import MartReader
from .raw_store import RawStore
from .runs import RunRecorder
from .schemas import DatasetCheck, DatasetSpec, FeatureSpec, MartPartition, MartPartitionMeta

__all__ = [
    "AkshareConnector",
    "ConnectorRegistry",
    "DatasetCatalog",
    "DatasetCheck",
    "DatasetSpec",
    "EvidenceRecord",
    "EvidenceStore",
    "FeatureBuilder",
    "FeatureRegistry",
    "FeatureSpec",
    "FeatureStore",
    "KnowledgeStore",
    "MartPublisher",
    "MartPartition",
    "MartPartitionMeta",
    "MartReader",
    "RawStore",
    "RunRecorder",
    "TushareConnector",
    "default_dataset_specs",
    "default_feature_specs",
]
