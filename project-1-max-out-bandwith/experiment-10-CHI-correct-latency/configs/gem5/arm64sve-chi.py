from gem5.components.boards.arm_board import ArmBoard
from gem5.components.boards.abstract_board import AbstractBoard
from gem5.components.memory.memory import ChanneledMemory
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import (
    SimpleSwitchableProcessor,
)
from gem5.isas import ISA
from gem5.utils.requires import requires
from gem5.utils.override import overrides
from gem5.resources.resource import Resource, CustomResource, DiskImageResource
from gem5.simulate.simulator import Simulator
from gem5.simulate.exit_event import ExitEvent

import m5
from m5.objects import DDR4_2400_16x4
from m5.objects import ArmDefaultRelease
from m5.objects import VExpress_GEM5_Foundation

from gem5_components.vector_cores.vector_cores import ARM_SVE_Parameters, SimpleSwitchableVectorProcessor

from saga.cache_hierarchy import SagaCacheHierarchy

from pathlib import Path

requires(isa_required=ISA.ARM)

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--vlen", type=int, help="SVE length", required=True)
parser.add_argument("--num_ccds", type=int, help="Number of cores", required=True)
parser.add_argument("--command", type=str, help="Command inputted to the guest system", required=True)
parser.add_argument("--enable_prefetcher", type=str, choices=["True", "False"], help="\"True\" if the prefetcher to L1 should be enable, \"False\" otherwise", required=True)
parser.add_argument("--num_channels", type=int, help="Number of memory channels", required=True)
parser.add_argument("--disk_image", type=str, help="Path to the disk image", required=True)
parser.add_argument("--hostname", type=str, help="Does not affect simulation, but for metadata recording", required=True)
args = parser.parse_args()

num_ccds = args.num_ccds
num_cores = 8 * num_ccds
command = args.command
vlen = args.vlen
enable_prefetcher = True if args.enable_prefetcher == "True" else False
disk_image_path = args.disk_image
hostname = args.hostname

cache_hierarchy = SagaCacheHierarchy()

memory = ChanneledMemory(
    dram_interface_class = DDR4_2400_16x4,
    num_channels = 2,
    interleaving_size = 2**8,
    size = "16GiB",
    addr_mapping = None
)

sve_parameters = ARM_SVE_Parameters(vlen = vlen, is_fullsystem = True)
processor = SimpleSwitchableVectorProcessor(
    starting_core_type = CPUTypes.ATOMIC,
    switch_core_type = CPUTypes.O3,
    isa = ISA.ARM,
    num_cores = num_cores,
    isa_vector_parameters = sve_parameters
)

class HighPerformanceArmBoard(ArmBoard):
    def __init__(
        self, clk_freq, processor, memory, cache_hierarchy, platform, release
    ):
        super().__init__(clk_freq, processor, memory, cache_hierarchy, platform, release)

    @overrides(ArmBoard)
    def _pre_instantiate(self):
        super()._pre_instantiate()
        for core_complex in self.cache_hierarchy.core_complexes:
            for core_cluster in core_complex.core_clusters:
                core_cluster.dcache.cache.dataAccessLatency = 5
                core_cluster.l2cache.cache.dataAccessLatency = 12
            core_complex.l3cache.cache.dataAccessLatency = 46

    @overrides(ArmBoard)
    def get_default_kernel_args(self):
        return [
            "console=ttyAMA0",
            "lpj=19988480",
            "norandmaps",
            "root=/dev/vda1",
            "rw",
            f"mem={self.get_memory().get_size()}",
            "init=/root/gem5-init.sh",
        ]
release = ArmDefaultRelease()
platform = VExpress_GEM5_Foundation()

# Setup the board.
board = HighPerformanceArmBoard(
    clk_freq="4GHz",
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
    release=release,
    platform=platform,
)

sve_parameters.apply_system_change(board)

# Set the Full System workload.
board.set_kernel_disk_workload(
    kernel=Resource("arm64-linux-kernel-5.10.110"),
    #disk_image=DiskImageResource("/projects/gem5/hn/DISK_IMAGES/arm64sve-hpc-2204-20230526.img"),
    disk_image=DiskImageResource(disk_image_path),
    bootloader=Resource("arm64-bootloader-foundation"),
    readfile_contents=f"{command}",
)

def handle_work_begin():
    print(f"Exit due to m5_work_begin()")
    print(f"info: Resetting stats")
    m5.stats.reset()
    print(f"info: Switching CPU")
    processor.switch()
    yield False

def handle_work_end():
    print(f"Exit due to m5_work_end()")
    print(f"info: Dumping stats")
    m5.stats.dump()
    yield False

def handle_exit():
    print(f"Exit due to m5_exit()")
    yield True

simulator = Simulator(
    board=board,
    on_exit_event={
        ExitEvent.WORKBEGIN: handle_work_begin(), # save checkpoint here
        ExitEvent.WORKEND: handle_work_end(),
        ExitEvent.EXIT: handle_exit()
    }
)
print("Beginning simulation!")
simulator.run()
