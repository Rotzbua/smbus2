from ctypes import (
    c_uint8,
    c_uint16,
    c_uint32,
    pointer,
)
from typing import Type

LP_c_uint8: Type[pointer[c_uint8]]
LP_c_uint16: Type[pointer[c_uint16]]
LP_c_uint32: Type[pointer[c_uint32]]


# class i2c_msg(Structure):
    # def __iter__(self) -> int: ...# incompatible type

# class SMBus:
#     force: bool = ... # does not exist
