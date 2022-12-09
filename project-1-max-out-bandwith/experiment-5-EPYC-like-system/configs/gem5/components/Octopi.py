from gem5.components.cachehierarchies.ruby.abstract_ruby_cache_hierarchy import AbstractRubyCacheHierarchy
from gem5.components.cachehierarchies.abstract_three_level_cache_hierarchy import (
    AbstractThreeLevelCacheHierarchy,
)
from gem5.coherence_protocol import CoherenceProtocol
from gem5.isas import ISA
from gem5.components.boards.abstract_board import AbstractBoard
from gem5.utils.requires import requires

from gem5.components.cachehierarchies.ruby.caches.mesi_three_level.directory import Directory
from gem5.components.cachehierarchies.ruby.caches.mesi_three_level.dma_controller import DMAController

from m5.objects import RubySystem, DMASequencer, RubyPortProxy

from .core_complex import CoreComplex
from .octopi_network import OctopiNetwork
from .ruby_network_components import RubyNetworkComponent, RubyRouter, RubyExtLink, RubyIntLink

# CoreComplex sub-systems own the L1, L2, L3 controllers
# OctopiCache owns the directory controllers
# RubySystem owns the DMA Controllers
class OctopiCache(AbstractRubyCacheHierarchy, AbstractThreeLevelCacheHierarchy):
    def __init__(
        self,
        l1i_size: str,
        l1i_assoc: int,
        l1d_size: str,
        l1d_assoc: int,
        l2_size: str,
        l2_assoc: int,
        l3_size: str,
        l3_assoc: int,
        num_core_complexes: int,
    ):
        AbstractRubyCacheHierarchy.__init__(self=self)
        AbstractThreeLevelCacheHierarchy.__init__(
            self=self,
            l1i_size=l1i_size,
            l1i_assoc=l1i_assoc,
            l1d_size=l1d_size,
            l1d_assoc=l1d_assoc,
            l2_size=l2_size,
            l2_assoc=l2_assoc,
            l3_size=l3_size,
            l3_assoc=l3_assoc,
        )

        self._directory_controllers = []
        self._core_complexes = []
        self._num_core_complexes = num_core_complexes

    def incorporate_cache(self, board: AbstractBoard) -> None:

        requires(
            coherence_protocol_required=CoherenceProtocol.MESI_THREE_LEVEL
        )

        cache_line_size = board.get_cache_line_size()

        self.ruby_system = RubySystem()
        # MESI_Three_Level needs 3 virtual networks
        self.ruby_system.number_of_virtual_networks = 3
        self.ruby_system.network = OctopiNetwork(self.ruby_system)

        # Setting up the core complex
        all_cores = board.get_processor().get_cores()
        num_cores_per_core_complex = len(all_cores) // self._num_core_complexes

        self.core_complexes = [CoreComplex(
                board = board,
                cores = all_cores[core_complex_idx*num_cores_per_core_complex:(core_complex_idx + 1) * num_cores_per_core_complex],
                ruby_system = self.ruby_system,
                l1i_size = self._l1i_size,
                l1i_assoc = self._l1i_assoc,
                l1d_size = self._l1d_size,
                l1d_assoc = self._l1d_assoc,
                l2_size = self._l2_size,
                l2_assoc = self._l2_assoc,
                l3_size = self._l3_size,
                l3_assoc = self._l3_assoc,
        ) for core_complex_idx, (address_range, mem_port) in enumerate(board.get_mem_ports())]

        self.ruby_system.network.incorporate_ccds(self.core_complexes)

        self._create_directory_controllers(board)
        self._create_dma_controllers(board, self.ruby_system)

        self.ruby_system.num_of_sequencers = len(all_cores) + len(self._dma_controllers)
        # SimpleNetwork requires .int_links and .routers to exist
        # if we want to call SimpleNetwork.setup_buffers()
        self.ruby_system.network.int_links = self.ruby_system.network._int_links
        self.ruby_system.network.ext_links = self.ruby_system.network._ext_links
        self.ruby_system.network.routers = self.ruby_system.network._routers
        self.ruby_system.network.setup_buffers()

        # Set up a proxy port for the system_port. Used for load binaries and
        # other functional-only things.
        self.ruby_system.sys_port_proxy = RubyPortProxy()
        board.connect_system_port(self.ruby_system.sys_port_proxy.in_ports)

    def _create_directory_controllers(self, board):
        # Adding controllers
        self.directory_controllers = [Directory(
                self.ruby_system.network, board.get_cache_line_size(), addr_range, mem_port
            ) for addr_range, mem_port in board.get_mem_ports()
        ]
        for ctrl in self.directory_controllers:
            ctrl.ruby_system = self.ruby_system
        # Adding controller routers
        self.directory_controller_routers = [RubyRouter(self.ruby_system.network) for _ in range(len(self.directory_controllers))]
        for router in self.directory_controller_routers:
            self.ruby_system.network._add_router(router)
        # Adding an external link for each controller and its router
        self.directory_controller_ext_links = [RubyExtLink(ext_node=dir_ctrl, int_node=dir_router) for dir_ctrl, dir_router in zip(self.directory_controllers, self.directory_controller_routers)]
        for ext_link in self.directory_controller_ext_links:
            self.ruby_system.network._add_ext_link(ext_link)
        _directory_controller_int_links = []
        for router in self.directory_controller_routers:
            int_link_1, int_link_2 = RubyIntLink.create_bidirectional_links(router, self.ruby_system.network.cross_ccd_router)
            _directory_controller_int_links.extend([int_link_1, int_link_2])
            self.ruby_system.network._add_int_link(int_link_1)
            self.ruby_system.network._add_int_link(int_link_2)
        self.directory_controller_int_links = _directory_controller_int_links

    def _create_dma_controllers(self, board, ruby_system):
        self._dma_controllers = []
        if board.has_dma_ports():
            dma_ports = board.get_dma_ports()
            for i, port in enumerate(dma_ports):
                ctrl = DMAController(self.ruby_system.network, cache_line_size)
                ctrl.dma_sequencer = DMASequencer(version=i, in_ports=port)
                self._dma_controllers.append(ctrl)
                ctrl.ruby_system = self.ruby_system
                self.ruby_system.network.connect_router_to_cross_ccd_router()
            ruby_system.dma_controllers = self._dma_controllers
