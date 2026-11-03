"""Shared constants for IDX/WPK readers."""

IDX_HEAD_SIZE = 0x20
IDX_REC_SIZE = 0x24
IDX_REC_SIZE_COMPACT = 0x1C  # 28-byte records (e.g. static.idx)
WPK_MAGIC = b"FKPW"
EMBEDDED_MAGIC = b"1DPW"
MIN_EMBEDDED_HEADER_SIZE = 0x30
