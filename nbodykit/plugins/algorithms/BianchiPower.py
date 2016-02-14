from nbodykit.extensionpoints import Algorithm, plugin_isinstance
from nbodykit.extensionpoints import DataSource, Transfer, Painter, datasources
from nbodykit.plugins import add_plugin_list_argument

import numpy
import logging
import os

def TracerCatalog(s):
    if isinstance(s, list):
        s = "::".join(map(str, s))
    return datasources.TracerCatalog.fromstring(s)

class BianchiPowerAlgorithm(Algorithm):
    """
    Algorithm to compute the power spectrum multipoles using FFTs
    for a data survey with non-trivial geometry
    """
    plugin_name = "BianchiPower"
    logger = logging.getLogger(plugin_name)

    @classmethod
    def register(kls):
        p = kls.parser
        p.description = "galaxy survey power spectrum multipole calculator via FFT"

        # the required arguments
        p.add_argument("input", type=TracerCatalog,
            help='the `DataSource` specifiying the data + randoms catalog to read')
        p.add_argument("Nmesh", type=int,
            help='the number of cells in the gridded mesh')
        add_plugin_list_argument(p, 'poles', type=lambda l: [int(s) for s in l],
            help='the multipoles to compute; values must be in [0,2,4]')

        # the optional arguments
        p.add_argument("--dk", type=float,
            help='the spacing of k bins to use; if not provided, the fundamental mode of the box is used')
        p.add_argument("--kmin", type=float, default=0.,
            help='the edge of the first `k` bin to use; default is 0')
        p.add_argument('-q', '--quiet', action="store_const", dest="log_level",
            default=logging.DEBUG, help="silence the logging output", const=logging.ERROR)
        
    def run(self):
        """
        Run the algorithm, which computes and returns the power spectrum
        """
        from nbodykit import measurestats
        from pmesh.particlemesh import ParticleMesh
                
        self.logger.setLevel(self.log_level)
        if self.comm.rank == 0: self.logger.info('importing done')

        # setup the particle mesh object, taking BoxSize from the painters
        pm = ParticleMesh(self.input.BoxSize, self.Nmesh, dtype='f4', comm=self.comm)

        # measure
        poles, meta = measurestats.compute_bianchi_poles(self.input, pm, comm=self.comm, log_level=self.log_level)
        k3d = pm.k

        # binning in k out to the minimum nyquist frequency
        # (accounting for possibly anisotropic box)
        dk = 2*numpy.pi/pm.BoxSize.min() if self.dk is None else self.dk
        kedges = numpy.arange(self.kmin, numpy.pi*pm.Nmesh/pm.BoxSize.max() + dk/2, dk)

        # project on to 1d k basis
        muedges = numpy.linspace(0, 1, 2, endpoint=True)
        edges = [kedges, muedges]
        poles_final = []
        for p in poles:
            
            # result is (k, mu, power, modes)
            result, _ = measurestats.project_to_basis(pm.comm, k3d, p, edges, symmetric=True)
            poles_final.append(numpy.squeeze(result[2]))
            
        # return (k, poles, modes)
        poles_final = numpy.stack(poles_final)
        k = numpy.squeeze(result[0])
        modes = numpy.squeeze(result[-1])
        result = k, poles_final, modes

        # compute the metadata to return
        Lx, Ly, Lz = pm.BoxSize
        meta.update({'Lx':Lx, 'Ly':Ly, 'Lz':Lz, 'volume':Lx*Ly*Lz})

        # return all the necessary results
        return kedges, result, meta

    def save(self, output, result):
        """
        Save the power spectrum results to the specified output file

        Parameters
        ----------
        output : str
            the string specifying the file to save
        result : tuple
            the tuple returned by `run()` -- first argument specifies the bin
            edges and the second is a dictionary holding the data results
        """
        from nbodykit.extensionpoints import MeasurementStorage
        
        # only the master rank writes
        if self.comm.rank == 0:
            
            kedges, result, meta = result
            k, poles, N = result
            
            # write binned statistic
            self.logger.info('measurement done; saving result to %s' %output)
            cols = ['k'] + ['power_%d' %l for l in [0, 2, 4]] + ['modes']
            pole_result = [k] + [pole for pole in poles] + [N]
            
            storage = MeasurementStorage.new('1d', output)
            storage.write(kedges, cols, pole_result, **meta)

