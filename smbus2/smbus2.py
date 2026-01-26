"""smbus2 - A drop-in replacement for smbus-cffi/smbus-python"""
# The MIT License (MIT)
# Copyright (c) 2020 Karl-Petter Lindegaard
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import annotations

import os
from ctypes import (
    POINTER,
    Array,
    Structure,
    Union,
    c_char,
    c_uint8,
    c_uint16,
    c_uint32,
    c_ulong,
    create_string_buffer,
    pointer,
    string_at,
)
from fcntl import ioctl
from types import TracebackType
from typing import Iterable, Sequence, SupportsBytes

# Commands from uapi/linux/i2c-dev.h
I2C_SLAVE = 0x0703  # Use this slave address
I2C_SLAVE_FORCE = 0x0706  # Use this slave address, even if it is already in use by a driver!
I2C_FUNCS = 0x0705  # Get the adapter functionality mask
I2C_RDWR = 0x0707  # Combined R/W transfer (one STOP only)
I2C_SMBUS = 0x0720  # SMBus transfer. Takes pointer to i2c_smbus_ioctl_data
I2C_PEC = 0x0708  # != 0 to use PEC with SMBus

# SMBus transfer read or write markers from uapi/linux/i2c.h
I2C_SMBUS_WRITE = 0
I2C_SMBUS_READ = 1

# Size identifiers uapi/linux/i2c.h
I2C_SMBUS_QUICK = 0
I2C_SMBUS_BYTE = 1
I2C_SMBUS_BYTE_DATA = 2
I2C_SMBUS_WORD_DATA = 3
I2C_SMBUS_PROC_CALL = 4
I2C_SMBUS_BLOCK_DATA = 5  # This isn't supported by Pure-I2C drivers with SMBUS emulation, like those in RaspberryPi, OrangePi, etc :(
I2C_SMBUS_BLOCK_PROC_CALL = 7  # Like I2C_SMBUS_BLOCK_DATA, it isn't supported by Pure-I2C drivers either.
I2C_SMBUS_I2C_BLOCK_DATA = 8
I2C_SMBUS_BLOCK_MAX = 32

# To determine what functionality is present (uapi/linux/i2c.h)
try:
    from enum import IntFlag
except ImportError:
    IntFlag = int


class I2cFunc(IntFlag):
    """
    These flags identify the operations supported by an I2C/SMBus device.

    You can test these flags on your `smbus.funcs`

    On newer python versions, I2cFunc is an IntFlag enum, but it
    falls back to class with a bunch of int constants on older releases.
    """
    I2C = 0x00000001
    ADDR_10BIT = 0x00000002
    PROTOCOL_MANGLING = 0x00000004  # I2C_M_IGNORE_NAK etc.
    SMBUS_PEC = 0x00000008
    NOSTART = 0x00000010  # I2C_M_NOSTART
    SLAVE = 0x00000020
    SMBUS_BLOCK_PROC_CALL = 0x00008000  # SMBus 2.0
    SMBUS_QUICK = 0x00010000
    SMBUS_READ_BYTE = 0x00020000
    SMBUS_WRITE_BYTE = 0x00040000
    SMBUS_READ_BYTE_DATA = 0x00080000
    SMBUS_WRITE_BYTE_DATA = 0x00100000
    SMBUS_READ_WORD_DATA = 0x00200000
    SMBUS_WRITE_WORD_DATA = 0x00400000
    SMBUS_PROC_CALL = 0x00800000
    SMBUS_READ_BLOCK_DATA = 0x01000000
    SMBUS_WRITE_BLOCK_DATA = 0x02000000
    SMBUS_READ_I2C_BLOCK = 0x04000000  # I2C-like block xfer
    SMBUS_WRITE_I2C_BLOCK = 0x08000000  # w/ 1-byte reg. addr.
    SMBUS_HOST_NOTIFY = 0x10000000

    SMBUS_BYTE = 0x00060000
    SMBUS_BYTE_DATA = 0x00180000
    SMBUS_WORD_DATA = 0x00600000
    SMBUS_BLOCK_DATA = 0x03000000
    SMBUS_I2C_BLOCK = 0x0c000000
    SMBUS_EMUL = 0x0eff0008


# i2c_msg flags from uapi/linux/i2c.h
I2C_M_RD = 0x0001

# Pointer definitions
LP_c_uint8 = POINTER(c_uint8)
LP_c_uint16 = POINTER(c_uint16)
LP_c_uint32 = POINTER(c_uint32)


#############################################################
# Type definitions as in i2c.h


class i2c_smbus_data(Array):
    """
    Adaptation of the i2c_smbus_data union in ``i2c.h``.

    Data for SMBus messages.
    """
    _length_ = I2C_SMBUS_BLOCK_MAX + 2
    _type_ = c_uint8


class union_i2c_smbus_data(Union):
    _fields_ = [
        ("byte", c_uint8),
        ("word", c_uint16),
        ("block", i2c_smbus_data)
    ]


union_pointer_type: pointer[union_i2c_smbus_data] = POINTER(union_i2c_smbus_data)


class i2c_smbus_ioctl_data(Structure):
    """
    As defined in ``i2c-dev.h``.
    """
    _fields_ = [
        ("read_write", c_uint8),
        ("command", c_uint8),
        ("size", c_uint32),
        ("data", union_pointer_type)]
    __slots__ = [name for name, type in _fields_]

    @staticmethod
    def create(read_write: int = I2C_SMBUS_READ, command: int = 0, size: int = I2C_SMBUS_BYTE_DATA) -> i2c_smbus_ioctl_data:
        u = union_i2c_smbus_data()
        return i2c_smbus_ioctl_data(
            read_write=read_write, command=command, size=size,
            data=union_pointer_type(u))


#############################################################
# Type definitions for i2c_rdwr combined transactions


class i2c_msg(Structure):
    """
    Represents a single I2C message for read or write operations.
    This is the expected data container for :py:meth:`SMBus.i2c_rdwr`.

    As defined in ``i2c.h``.
    """
    _fields_ = [
        ("addr", c_uint16),
        ("flags", c_uint16),
        ("len", c_uint16),
        ("buf", POINTER(c_char))]

    def __iter__(self):
        """ Iterator / Generator

        :return: iterates over :py:attr:`buf`
        :rtype: :py:class:`generator` which returns int values
        """
        idx = 0
        while idx < self.len:
            yield ord(self.buf[idx])
            idx += 1

    def __len__(self) -> int:
        return self.len

    def __bytes__(self) -> bytes:
        return string_at(self.buf, self.len)

    def __repr__(self) -> str:
        return "i2c_msg(%d,%d,%r)" % (self.addr, self.flags, self.__bytes__())

    def __str__(self) -> str:
        s = self.__bytes__()
        # Throw away non-decodable bytes
        s = s.decode(errors="ignore")
        return s

    @staticmethod
    def read(address: int, length: int) -> i2c_msg:
        """
        Prepares an i2c read transaction.

        :param address: Slave address.
        :param length: Number of bytes to read.
        :return: New :py:class:`i2c_msg` instance for read operation.
        """
        arr = create_string_buffer(length)
        return i2c_msg(
            addr=address, flags=I2C_M_RD, len=length,
            buf=arr)

    @staticmethod
    def write(address: int, buf: str | Iterable[int] | SupportsBytes) -> i2c_msg:
        """
        Prepares an i2c write transaction.

        :param address: Slave address.
        :param buf: Bytes to write. Either list of values or str.
        :return: New :py:class:`i2c_msg` instance for write operation.
        """
        if type(buf) is str:
            buf = bytes(map(ord, buf))
        else:
            buf = bytes(buf)

        arr = create_string_buffer(buf, len(buf))
        return i2c_msg(
            addr=address, flags=0, len=len(arr),
            buf=arr)


class i2c_rdwr_ioctl_data(Structure):
    """
    As defined in ``i2c-dev.h``.
    """
    _fields_ = [
        ("msgs", POINTER(i2c_msg)),
        ("nmsgs", c_uint32)
    ]
    __slots__ = [name for name, type in _fields_]

    @staticmethod
    def create(*i2c_msg_instances: Sequence[i2c_msg]) -> i2c_rdwr_ioctl_data:
        """
        Factory method for creating an i2c_rdwr_ioctl_data struct that can
        be called with ``ioctl(fd, I2C_RDWR, data)``.

        :param i2c_msg_instances: Up to 42 i2c_msg instances
        """
        n_msg = len(i2c_msg_instances)
        msg_array = (i2c_msg * n_msg)(*i2c_msg_instances)
        return i2c_rdwr_ioctl_data(
            msgs=msg_array,
            nmsgs=n_msg
        )


#############################################################


class SMBus:
    """
    Main class for I2C and SMBus communication, providing all IO functions for device access.
    """

    def __init__(self, bus: None | int | str = None, force: bool = False) -> None:
        """
        Initialize and (optionally) open an i2c bus connection.

        :param bus: i2c bus number (e.g. 0 or 1)
            or an absolute file path (e.g. `/dev/i2c-42`).
            If not given, a subsequent  call to ``open()`` is required.
        :param force: Use slave address even when driver is already using it.
        """
        self.fd: int | None = None
        self.funcs:I2cFunc = I2cFunc(0)
        if bus is not None:
            self.open(bus)
        self.address:int|None = None
        self.force:bool = force
        self._force_last = None
        self._pec = 0

    def __enter__(self) -> SMBus:
        """Enter handler."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit handler."""
        self.close()

    def open(self, bus: int | str) -> None:
        """
        Open a given i2c bus.

        :param bus: i2c bus number (e.g. 0 or 1)
            or an absolute file path (e.g. '/dev/i2c-42').
        :raise TypeError: if type(bus) is not in (int, str)
        """
        if isinstance(bus, int):
            filepath = f"/dev/i2c-{bus}"
        elif isinstance(bus, str):
            filepath = bus
        else:
            raise TypeError(f"Unexpected type(bus)={type(bus)}")

        self.fd = os.open(filepath, os.O_RDWR)
        self.funcs = self._get_funcs()

    def close(self) -> None:
        """
        Close the i2c connection.
        """
        if self.fd:
            os.close(self.fd)
            self.fd = None
            self._pec = 0
            self.address = None
            self._force_last = None

    def _get_pec(self):
        return self._pec

    def enable_pec(self, enable: bool = True) -> None:
        """
        Enable/Disable PEC (Packet Error Checking) - SMBus 1.1 and later
        """
        if not (self.funcs & I2cFunc.SMBUS_PEC):
            raise OSError("SMBUS_PEC is not a feature")
        self._pec = int(enable)
        ioctl(self.fd, I2C_PEC, self._pec)

    pec: int = property(_get_pec, enable_pec)  # Drop-in replacement for smbus member "pec"
    """Get and set SMBus PEC. 0 = disabled (default), 1 = enabled."""

    def _set_address(self, address, force=None):
        """
        Set i2c slave address to use for subsequent calls.

        :param address:
        :type address: int
        :param force: Use slave address even when driver is already using it.
        :type force: bool
        """
        force = force if force is not None else self.force
        if self.address != address or self._force_last != force:
            if force is True:
                ioctl(self.fd, I2C_SLAVE_FORCE, address)
            else:
                ioctl(self.fd, I2C_SLAVE, address)
            self.address = address
            self._force_last = force

    def _get_funcs(self):
        """
        Returns a value stating supported I2C functions.

        :rtype: int
        """
        # Use c_ulong to match the kernel's expected "unsigned long *" type.
        # This is 4 bytes on 32-bit systems and 8 bytes on 64-bit systems.
        # Using c_uint32 on 64-bit systems causes buffer overflow errors
        # in Python 3.14+ due to stricter ctypes buffer size checks.
        f = c_ulong()
        ioctl(self.fd, I2C_FUNCS, f)
        return f.value

    def write_quick(self, i2c_addr: int, force: bool | None = None) -> None:
        """
        Perform quick transaction. Throws IOError if unsuccessful.
        :param i2c_addr: i2c address
        :type i2c_addr: int
        :param force: Use slave address even when driver is already using it.
        :type force: bool
        """
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_WRITE, command=0, size=I2C_SMBUS_QUICK)
        ioctl(self.fd, I2C_SMBUS, msg)

    def read_byte(self, i2c_addr: int, force: bool | None = None) -> int:
        """
        Read a single byte from a device.

        :param i2c_addr: i2c address
        :param force: Use slave address even when driver is already using it.
        :return: Read byte value
        """
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_READ, command=0, size=I2C_SMBUS_BYTE
        )
        ioctl(self.fd, I2C_SMBUS, msg)
        return msg.data.contents.byte

    def write_byte(self, i2c_addr: int, value: int, force: bool | None = None) -> None:
        """
        Write a single byte to a device.

        :param i2c_addr: i2c address
        :param value: value to write
        :param force: Use slave address even when driver is already using it.
        """
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_WRITE, command=value, size=I2C_SMBUS_BYTE
        )
        ioctl(self.fd, I2C_SMBUS, msg)

    def read_byte_data(self, i2c_addr: int, register: int, force: bool | None = None) -> int:
        """
        Read a single byte from a designated register.

        :param i2c_addr: i2c address
        :param register: Register to read
        :param force: Use slave address even when driver is already using it.
        :return: Read byte value
        """
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_READ, command=register, size=I2C_SMBUS_BYTE_DATA
        )
        ioctl(self.fd, I2C_SMBUS, msg)
        return msg.data.contents.byte

    def write_byte_data(self, i2c_addr: int, register: int, value: int, force: bool | None = None) -> None:
        """
        Write a byte to a given register.

        :param i2c_addr: i2c address
        :param register: Register to write to
        :param value: Byte value to transmit
        :param force: Use slave address even when driver is already using it.
        """
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_WRITE, command=register, size=I2C_SMBUS_BYTE_DATA
        )
        msg.data.contents.byte = value
        ioctl(self.fd, I2C_SMBUS, msg)

    def read_word_data(self, i2c_addr: int, register: int, force: bool | None = None) -> int:
        """
        Read a single word (2 bytes) from a given register.

        :param i2c_addr: i2c address
        :param register: Register to read
        :param force: Use slave address even when driver is already using it.
        :return: 2-byte word
        """
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_READ, command=register, size=I2C_SMBUS_WORD_DATA
        )
        ioctl(self.fd, I2C_SMBUS, msg)
        return msg.data.contents.word

    def write_word_data(self, i2c_addr: int, register: int, value: int, force: bool | None = None) -> None:
        """
        Write a single word (2 bytes) to a given register.

        :param i2c_addr: i2c address
        :param register: Register to write to
        :param value: Word value to transmit
        :param force: Use slave address even when driver is already using it.
        """
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_WRITE, command=register, size=I2C_SMBUS_WORD_DATA
        )
        msg.data.contents.word = value
        ioctl(self.fd, I2C_SMBUS, msg)

    def process_call(self, i2c_addr: int, register: int, value: int, force: bool | None = None) -> int:
        """
        Executes a SMBus Process Call, sending a 16-bit value and receiving a 16-bit response

        :param i2c_addr: i2c address
        :param register: Register to read/write to
        :param value: Word value to transmit
        :param force: Use slave address even when driver is already using it.
        """
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_WRITE, command=register, size=I2C_SMBUS_PROC_CALL
        )
        msg.data.contents.word = value
        ioctl(self.fd, I2C_SMBUS, msg)
        return msg.data.contents.word

    def read_block_data(self, i2c_addr: int, register: int, force: bool | None = None) -> list[int]:
        """
        Read a block of up to 32-bytes from a given register.

        :param i2c_addr: i2c address
        :param register: Start register
        :param force: Use slave address even when driver is already using it.
        :return: List of bytes
        """
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_READ, command=register, size=I2C_SMBUS_BLOCK_DATA
        )
        ioctl(self.fd, I2C_SMBUS, msg)
        length = msg.data.contents.block[0]
        return msg.data.contents.block[1:length + 1]

    def write_block_data(
        self,
        i2c_addr: int,
        register: int,
        data: Sequence[int],
        force: bool | None = None,
    ) -> None:
        """
        Write a block of byte data to a given register.

        :param i2c_addr: i2c address
        :param register: Start register
        :param data: List of bytes
        :param force: Use slave address even when driver is already using it.
        """
        length = len(data)
        if length > I2C_SMBUS_BLOCK_MAX:
            raise ValueError("Data length cannot exceed %d bytes" % I2C_SMBUS_BLOCK_MAX)
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_WRITE, command=register, size=I2C_SMBUS_BLOCK_DATA
        )
        msg.data.contents.block[0] = length
        msg.data.contents.block[1:length + 1] = data
        ioctl(self.fd, I2C_SMBUS, msg)

    def block_process_call(
        self,
        i2c_addr: int,
        register: int,
        data: Sequence[int],
        force: bool | None = None,
    ) -> list[int]:
        """
        Executes a SMBus Block Process Call, sending a variable-size data
        block and receiving another variable-size response

        :param i2c_addr: i2c address
        :param register: Register to read/write to
        :param data: List of bytes
        :param force: Use slave address even when driver is already using it.
        :return: List of bytes
        """
        length = len(data)
        if length > I2C_SMBUS_BLOCK_MAX:
            raise ValueError("Data length cannot exceed %d bytes" % I2C_SMBUS_BLOCK_MAX)
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_WRITE, command=register, size=I2C_SMBUS_BLOCK_PROC_CALL
        )
        msg.data.contents.block[0] = length
        msg.data.contents.block[1:length + 1] = data
        ioctl(self.fd, I2C_SMBUS, msg)
        length = msg.data.contents.block[0]
        return msg.data.contents.block[1:length + 1]

    def read_i2c_block_data(self, i2c_addr: int, register: int, length: int, force: bool | None = None) -> list[int]:
        """
        Read a block of byte data from a given register.

        :param i2c_addr: i2c address
        :param register: Start register
        :param length: Desired block length
        :param force: Use slave address even when driver is already using it.
        :return: List of bytes
        """
        if length > I2C_SMBUS_BLOCK_MAX:
            raise ValueError("Desired block length over %d bytes" % I2C_SMBUS_BLOCK_MAX)
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_READ, command=register, size=I2C_SMBUS_I2C_BLOCK_DATA
        )
        msg.data.contents.byte = length
        ioctl(self.fd, I2C_SMBUS, msg)
        return msg.data.contents.block[1:length + 1]

    def write_i2c_block_data(
            self,
            i2c_addr: int,
            register: int,
            data: Sequence[int],
            force: bool | None = None,
    ) -> None:
        """
        Write a block of byte data to a given register.

        :param i2c_addr: i2c address
        :param register: Start register
        :param data: List of bytes
        :param force: Use slave address even when driver is already using it.
        """
        length = len(data)
        if length > I2C_SMBUS_BLOCK_MAX:
            raise ValueError("Data length cannot exceed %d bytes" % I2C_SMBUS_BLOCK_MAX)
        self._set_address(i2c_addr, force=force)
        msg = i2c_smbus_ioctl_data.create(
            read_write=I2C_SMBUS_WRITE, command=register, size=I2C_SMBUS_I2C_BLOCK_DATA
        )
        msg.data.contents.block[0] = length
        msg.data.contents.block[1:length + 1] = data
        ioctl(self.fd, I2C_SMBUS, msg)

    def i2c_rdwr(self, *i2c_msgs: i2c_msg) -> None:
        """
        Combine a series of i2c read and write operations in a single
        transaction (with repeated start bits but no stop bits in between).

        This method takes i2c_msg instances as input, which must be created
        first with :py:meth:`i2c_msg.read` or :py:meth:`i2c_msg.write`.

        :param i2c_msgs: One or more i2c_msg class instances.
        """
        ioctl_data = i2c_rdwr_ioctl_data.create(*i2c_msgs)
        ioctl(self.fd, I2C_RDWR, ioctl_data)
