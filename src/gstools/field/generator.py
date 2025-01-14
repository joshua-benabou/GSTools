# -*- coding: utf-8 -*-
"""
GStools subpackage providing generators for spatial random fields.

.. currentmodule:: gstools.field.generator

The following classes are provided

.. autosummary::
   Generator
   RandMeth
   IncomprRandMeth
   IncomprRandZeroVelMeth
   GenericRandVectorFieldMeth
"""
# pylint: disable=C0103, W0222, C0412, W0231
import warnings
from abc import ABC, abstractmethod
from copy import deepcopy as dcp

import numpy as np

from gstools import config
from gstools.covmodel.base import CovModel
from gstools.random.rng import RNG

if config.USE_RUST:  # pragma: no cover
    # pylint: disable=E0401
    from gstools_core import summate, summate_incompr
else:
    from gstools.field.summator import summate, summate_incompr, summate_incompr_zero_vel, summate_generic_vector_field


__all__ = ["RandMeth", "IncomprRandMeth", "IncomprRandZeroVelMeth", "GenericRandVectorFieldMeth", ]



SAMPLING = ["auto", "inversion", "mcmc"]


class Generator(ABC):
    """
    Abstract generator class.

    Parameters
    ----------
    model : :any:`CovModel`
        Covariance model
    **kwargs
        Placeholder for keyword-args
    """

    @abstractmethod
    def __init__(self, model, **kwargs):
        pass

    @abstractmethod
    def update(self, model=None, seed=np.nan):
        """Update the model and the seed.

        If model and seed are not different, nothing will be done.

        Parameters
        ----------
        model : :any:`CovModel` or :any:`None`, optional
            covariance model. Default: :any:`None`
        seed : :class:`int` or :any:`None` or :any:`numpy.nan`, optional
            the seed of the random number generator.
            If :any:`None`, a random seed is used. If :any:`numpy.nan`,
            the actual seed will be kept. Default: :any:`numpy.nan`
        """

    @abstractmethod
    def get_nugget(self, shape):
        """
        Generate normal distributed values for the nugget simulation.

        Parameters
        ----------
        shape : :class:`tuple`
            the shape of the summed modes

        Returns
        -------
        nugget : :class:`numpy.ndarray`
            the nugget in the same shape as the summed modes
        """

    @abstractmethod
    def __call__(self, pos, add_nugget=True):
        """
        Generate the field.

        Parameters
        ----------
        pos : (d, n), :class:`numpy.ndarray`
            the position tuple with d dimensions and n points.
        add_nugget : :class:`bool`
            Whether to add nugget noise to the field.

        Returns
        -------
        :class:`numpy.ndarray`
            the random modes
        """

    @property
    @abstractmethod
    def value_type(self):
        """:class:`str`: Type of the field values (scalar, vector)."""

    @property
    def name(self):
        """:class:`str`: Name of the generator."""
        return self.__class__.__name__


class RandMeth(Generator):
    r"""Randomization method for calculating isotropic random fields.

    Parameters
    ----------
    model : :any:`CovModel`
        Covariance model
    mode_no : :class:`int`, optional
        Number of Fourier modes. Default: ``1000``
    seed : :class:`int` or :any:`None`, optional
        The seed of the random number generator.
        If "None", a random seed is used. Default: :any:`None`
    verbose : :class:`bool`, optional
        Be chatty during the generation.
        Default: :any:`False`
    sampling : :class:`str`, optional
        Sampling strategy. Either

            * "auto": select best strategy depending on given model
            * "inversion": use inversion method
            * "mcmc": use mcmc sampling

    **kwargs
        Placeholder for keyword-args

    Notes
    -----
    The Randomization method is used to generate isotropic
    spatial random fields characterized by a given covariance model.
    The calculation looks like [Hesse2014]_:

    .. math::
       u\left(x\right)=
       \sqrt{\frac{\sigma^{2}}{N}}\cdot
       \sum_{i=1}^{N}\left(
       Z_{1,i}\cdot\cos\left(\left\langle k_{i},x\right\rangle \right)+
       Z_{2,i}\cdot\sin\left(\left\langle k_{i},x\right\rangle \right)
       \right)

    where:

        * :math:`N` : fourier mode number
        * :math:`Z_{j,i}` : random samples from a normal distribution
        * :math:`k_i` : samples from the spectral density distribution of
          the covariance model

    References
    ----------
    .. [Hesse2014] Heße, F., Prykhodko, V., Schlüter, S., and Attinger, S.,
           "Generating random fields with a truncated power-law variogram:
           A comparison of several numerical methods",
           Environmental Modelling & Software, 55, 32-48., (2014)
    """

    def __init__(
        self,
        model,
        mode_no=1000,
        seed=None,
        verbose=False,
        sampling="auto",
        **kwargs,
    ):
        if kwargs:
            warnings.warn("gstools.RandMeth: **kwargs are ignored")
        # initialize atributes
        self._mode_no = int(mode_no)
        self._verbose = bool(verbose)
        # initialize private atributes
        self._model = None
        self._seed = None
        self._rng = None
        self._z_1 = None
        self._z_2 = None
        self._cov_sample = None
        self._value_type = "scalar"
        # set sampling strategy
        self._sampling = None
        self.sampling = sampling
        # set model and seed
        self.update(model, seed)

    def __call__(self, pos, add_nugget=True):
        """Calculate the random modes for the randomization method.

        This method  calls the `summate_*` Cython methods, which are the
        heart of the randomization method.

        Parameters
        ----------
        pos : (d, n), :class:`numpy.ndarray`
            the position tuple with d dimensions and n points.
        add_nugget : :class:`bool`
            Whether to add nugget noise to the field.

        Returns
        -------
        :class:`numpy.ndarray`
            the random modes
        """
        pos = np.asarray(pos, dtype=np.double)
        summed_modes = summate(self._cov_sample, self._z_1, self._z_2, pos)
        nugget = self.get_nugget(summed_modes.shape) if add_nugget else 0.0
        return np.sqrt(self.model.var / self._mode_no) * summed_modes + nugget

    def get_nugget(self, shape):
        """
        Generate normal distributed values for the nugget simulation.

        Parameters
        ----------
        shape : :class:`tuple`
            the shape of the summed modes

        Returns
        -------
        nugget : :class:`numpy.ndarray`
            the nugget in the same shape as the summed modes
        """
        if self.model.nugget > 0:
            nugget = np.sqrt(self.model.nugget) * self._rng.random.normal(
                size=shape
            )
        else:
            nugget = 0.0
        return nugget

    def update(self, model=None, seed=np.nan):
        """Update the model and the seed.

        If model and seed are not different, nothing will be done.

        Parameters
        ----------
        model : :any:`CovModel` or :any:`None`, optional
            covariance model. Default: :any:`None`
        seed : :class:`int` or :any:`None` or :any:`numpy.nan`, optional
            the seed of the random number generator.
            If :any:`None`, a random seed is used. If :any:`numpy.nan`,
            the actual seed will be kept. Default: :any:`numpy.nan`
        """

        # check if a new model is given
        if isinstance(model, CovModel):
            if self.model != model:
                self._model = dcp(model)
                if seed is None or not np.isnan(seed):
                    self.reset_seed(seed)
                else:
                    self.reset_seed(self._seed)
            # just update the seed, if its a new one
            elif seed is None or not np.isnan(seed):
                self.seed = seed
        # or just update the seed, when no model is given
        elif model is None and (seed is None or not np.isnan(seed)):
            if isinstance(self._model, CovModel):
                self.seed = seed
            else:
                raise ValueError(
                    "gstools.field.generator.RandMeth: no 'model' given"
                )
        # if the user tries to trick us, we beat him!
        elif model is None and np.isnan(seed):
            if (
                isinstance(self._model, CovModel)
                and self._z_1 is not None
                and self._z_2 is not None
                and self._cov_sample is not None
            ):
                if self.verbose:
                    print("RandMeth.update: Nothing will be done...")
            else:
                raise ValueError(
                    "gstools.field.generator.RandMeth: "
                    "neither 'model' nor 'seed' given!"
                )
        # wrong model type
        else:
            raise ValueError(
                "gstools.field.generator.RandMeth: 'model' is not an "
                "instance of 'gstools.CovModel'"
            )

    def reset_seed(self, seed=np.nan):
        """
        Recalculate the random amplitudes and wave numbers with the given seed.

        Parameters
        ----------
        seed : :class:`int` or :any:`None` or :any:`numpy.nan`, optional
            the seed of the random number generator.
            If :any:`None`, a random seed is used. If :any:`numpy.nan`,
            the actual seed will be kept. Default: :any:`numpy.nan`

        Notes
        -----
        Even if the given seed is the present one, modes will be recalculated.
        """
        if seed is None or not np.isnan(seed):
            self._seed = seed
        self._rng = RNG(self._seed)

        # normal distributed samples for randmeth
        self._z_1 = self._rng.random.normal(size=self._mode_no)
        self._z_2 = self._rng.random.normal(size=self._mode_no)
            
        # sample uniform on a sphere
        sphere_coord = self._rng.sample_sphere(self.model.dim, self._mode_no)
        # sample radii acording to radial spectral density of the model
        if self.sampling == "inversion" or (
            self.sampling == "auto" and self.model.has_ppf
        ):
            pdf, cdf, ppf = self.model.dist_func
            rad = self._rng.sample_dist(
                size=self._mode_no, pdf=pdf, cdf=cdf, ppf=ppf, a=0
            )

        else:
            rad = self._rng.sample_ln_pdf(
                ln_pdf=self.model.ln_spectral_rad_pdf,
                size=self._mode_no,
                sample_around=1.0 / self.model.len_rescaled,
            )

        # get fully spatial samples by multiplying sphere samples and radii
        self._cov_sample = rad * sphere_coord

    @property
    def sampling(self):
        """:class:`str`: Sampling strategy."""
        return self._sampling

    @sampling.setter
    def sampling(self, sampling):
        if sampling not in ["auto", "inversion", "mcmc"]:
            raise ValueError(f"RandMeth: sampling not in {SAMPLING}.")
        self._sampling = sampling

    @property
    def seed(self):
        """:class:`int`: Seed of the master RNG.

        Notes
        -----
        If a new seed is given, the setter property not only saves the
        new seed, but also creates new random modes with the new seed.
        """
        return self._seed

    @seed.setter
    def seed(self, new_seed):
        if new_seed is not self._seed:
            self.reset_seed(new_seed)

    @property
    def model(self):
        """:any:`CovModel`: Covariance model of the spatial random field."""
        return self._model

    @model.setter
    def model(self, model):
        self.update(model)

    @property
    def mode_no(self):
        """:class:`int`: Number of modes in the randomization method."""
        return self._mode_no

    @mode_no.setter
    def mode_no(self, mode_no):
        if int(mode_no) != self._mode_no:
            self._mode_no = int(mode_no)
            self.reset_seed(self._seed)

    @property
    def verbose(self):
        """:class:`bool`: Verbosity of the generator."""
        return self._verbose

    @verbose.setter
    def verbose(self, verbose):
        self._verbose = bool(verbose)

    @property
    def value_type(self):
        """:class:`str`: Type of the field values (scalar, vector)."""
        return self._value_type

    def __repr__(self):
        """Return String representation."""
        return (
            f"{self.name}(model={self.model}, "
            f"mode_no={self._mode_no}, seed={self.seed})"
        )


class IncomprRandMeth(RandMeth):
    r"""RandMeth for incompressible random vector fields.

    Parameters
    ----------
    model : :any:`CovModel`
        covariance model
    mean_velocity : :class:`float`, optional
        the mean velocity in x-direction
    mode_no : :class:`int`, optional
        number of Fourier modes. Default: ``1000``
    vec_dim : :class:`int`, optional
        vector dimension, in case it mismatches the model dimension
    seed : :class:`int` or :any:`None`, optional
        the seed of the random number generator.
        If "None", a random seed is used. Default: :any:`None`
    verbose : :class:`bool`, optional
        State if there should be output during the generation.
        Default: :any:`False`
    sampling : :class:`str`, optional
        Sampling strategy. Either

            * "auto": select best strategy depending on given model
            * "inversion": use inversion method
            * "mcmc": use mcmc sampling

    **kwargs
        Placeholder for keyword-args

    Notes
    -----
    The Randomization method is used to generate isotropic
    spatial incompressible random vector fields characterized
    by a given covariance model. The equation is [Kraichnan1970]_:

    .. math::
       u_i\left(x\right)= \bar{u_i} \delta_{i1} +
       \bar{u_i}\sqrt{\frac{\sigma^{2}}{N}}\cdot
       \sum_{j=1}^{N}p_i(k_{j})\left(
       Z_{1,j}\cdot\cos\left(\left\langle k_{j},x\right\rangle \right)+
       Z_{2,j}\cdot\sin\left(\left\langle k_{j},x\right\rangle \right)
       \right)

    where:

        * :math:`\bar u` : mean velocity in :math:`e_1` direction
        * :math:`N` : fourier mode number
        * :math:`Z_{k,j}` : random samples from a normal distribution
        * :math:`k_j` : samples from the spectral density distribution of
          the covariance model
        * :math:`p_i(k_j) = e_1 - \frac{k_i k_1}{k^2}` : the projector
          ensuring the incompressibility

    References
    ----------
    .. [Kraichnan1970] Kraichnan, R. H.,
           "Diffusion by a random velocity field.",
           The physics of fluids, 13(1), 22-31., (1970)
    """

    def __init__(
        self,
        model,
        mean_velocity=1.0,
        mode_no=1000,
        vec_dim=None,
        seed=None,
        verbose=False,
        sampling="auto",
        **kwargs,
    ):
        if vec_dim is None and (model.dim < 2 or model.dim > 3):

            raise ValueError(
                "Only 2D and 3D incompressible vectors can be generated."
            )
        if vec_dim is not None and (vec_dim < 2 or vec_dim > 3):
            raise ValueError(
                "Only 2D and 3D incompressible vectors can be generated."

            )
        super().__init__(model, mode_no, seed, verbose, sampling, **kwargs)

        self.mean_u = mean_velocity
        if vec_dim is None:
            self.vec_dim = model.dim
        else:
            self.vec_dim = vec_dim
        self._value_type = "vector"

    def __call__(self, pos, add_nugget=True):
        """Calculate the random modes for the randomization method.

        This method  calls the `summate_incompr_*` Cython methods,
        which are the heart of the randomization method.
        In this class the method contains a projector to
        ensure the incompressibility of the vector field.

        Parameters
        ----------
        pos : (d, n), :class:`numpy.ndarray`
            the position tuple with d dimensions and n points.
        add_nugget : :class:`bool`
            Whether to add nugget noise to the field.

        Returns
        -------
        :class:`numpy.ndarray`
            the random modes
        """
        pos = np.asarray(pos, dtype=np.double)
        summed_modes = summate_incompr(
            self.vec_dim, self._cov_sample, self._z_1, self._z_2, pos
        )
        nugget = self.get_nugget(summed_modes.shape) if add_nugget else 0.0
        e1 = self._create_unit_vector(summed_modes.shape)
        return (
            #self.mean_u * e1 #!!! Joshua has commented this out to get zero-velocity fields
            + self.mean_u
            * np.sqrt(self.model.var / self._mode_no)
            * summed_modes
            + nugget
        )

    def _create_unit_vector(self, broadcast_shape, axis=0):
        """Create a unit vector.

        Can be multiplied with a vector of shape broadcast_shape

        Parameters
        ----------
        broadcast_shape : :class:`tuple`
            the shape of the array with which
            the unit vector is to be multiplied
        axis : :class:`int`, optional
            the direction of the unit vector. Default: ``0``

        Returns
        -------
        :class:`numpy.ndarray`
            the unit vector
        """
        shape = np.ones(len(broadcast_shape), dtype=int)
        shape[0] = self.vec_dim

        e1 = np.zeros(shape)
        e1[axis] = 1.0
        return e1
    
class IncomprRandZeroVelMeth(RandMeth):
    r"""RandMeth for incompressible random vector fields with zero velocity, using Eq. 20 of Kraichnan (1970)

    Parameters
    ----------
    model : :any:`CovModel`
        covariance model
    mean_velocity : :class:`float`, optional
        the mean velocity in x-direction
    mode_no : :class:`int`, optional
        number of Fourier modes. Default: ``1000``
    vec_dim : :class:`int`, optional
        vector dimension, in case it mismatches the model dimension
    seed : :class:`int` or :any:`None`, optional
        the seed of the random number generator.
        If "None", a random seed is used. Default: :any:`None`
    verbose : :class:`bool`, optional
        State if there should be output during the generation.
        Default: :any:`False`
    sampling : :class:`str`, optional
        Sampling strategy. Either

            * "auto": select best strategy depending on given model
            * "inversion": use inversion method
            * "mcmc": use mcmc sampling

    **kwargs
        Placeholder for keyword-args

    Notes
    -----
    The Randomization method is used to generate isotropic
    spatial incompressible random vector fields characterized
    by a given covariance model. The equation is [Kraichnan1970]_:

    .. math::
       u\left(x\right)= 
       \bar{u}\sqrt{\frac{\sigma^{2}}{N}}\cdot
       \sum_{j=1}^{N}\left(
       W_{1,j}\cdot\cos\left(\left\langle k_{j},x\right\rangle \right)+
       W_{2,j}\cdot\sin\left(\left\langle k_{j},x\right\rangle \right)
       \right)

    where:

        * :math:`\bar u` : mean velocity in :math:`e_1` direction
        * :math:`N` : fourier mode number
        * :math:`Z_{k,j}` : random samples from a normal distribution, each of size (N,vec_dim)
        * :math:`k_j` : samples from the spectral density distribution of
          the covariance model
        * :math:`W_{k,j}` : cross-product of the random normal vector Z_{k,j} with the wave-vector k_j
          ensuring the incompressibility

    References
    ----------
    .. [Kraichnan1970] Kraichnan, R. H.,
           "Diffusion by a random velocity field.",
           The physics of fluids, 13(1), 22-31., (1970)
    """

    def __init__(
        self,
        model,
        mean_velocity=1.0,
        mode_no=1000,
        vec_dim=None,
        seed=None,
        verbose=False,
        sampling="auto",
        periodic_bc=False,
        box_len=None,
        **kwargs,
    ):
        if vec_dim is None and (model.dim < 2 or model.dim > 3):
            raise ValueError(
                "Only 2D and 3D incompressible vectors can be generated."
            )
        if vec_dim is not None and (vec_dim < 2 or vec_dim > 3):
            raise ValueError(
                "Only 2D and 3D incompressible vectors can be generated."
            )
        super().__init__(model, mode_no, seed, verbose, sampling, **kwargs)

        self.mean_u = mean_velocity
        if vec_dim is None:
            self.vec_dim = model.dim
        else:
            self.vec_dim = vec_dim
        self._value_type = "vector"
        
        self.periodic_bc=periodic_bc
        self.box_len=box_len

        # for using the Kraichnan method for zero-velocity fluid, z_1 and z_2 must instead contain N=self._mode_no independent realizations of normal vectors of size vec_dim,
        mean = np.zeros(self.vec_dim)
        cov = np.identity(self.vec_dim)

        self._z_1 = self._rng.random.multivariate_normal(mean, cov, size=self._mode_no) # shape (_mode_no, self.vec_dim)
        self._z_2 = self._rng.random.multivariate_normal(mean, cov, size=self._mode_no)
        print("shape of z_1: ",np.shape(self._z_1))
        
    def __call__(self, pos):
        """Calculate the random modes for the randomization method.

        This method  calls the `summate_incompr_*` Cython methods,
        which are the heart of the randomization method.
        In this class the method contains a projector to
        ensure the incompressibility of the vector field.

        Parameters
        ----------
        pos : (d, n), :class:`numpy.ndarray`
            the position tuple with d dimensions and n points.

        Returns
        -------
        :class:`numpy.ndarray`
            the random modes
        """
        pos = np.asarray(pos, dtype=np.double)
        
        print("\nStarting summate_incompr_zero_vel")

        if self.periodic_bc:
            if not self.box_len==None:
                print("\nimposing periodic boundary conditions on spatial coordinates!")
                print("\nrounding first 'vec_dim' vector components in cov_sample to multiples of 2pi/box_length")
                fac = 2*np.pi/np.array(self.box_len)
                self._cov_sample[:self.vec_dim,:] = fac[:,None]*np.round(self._cov_sample[:self.vec_dim,:]/fac[:,None])
            else:
                raise ValueError(
                "For periodic boundary conditions on spatial coordinates, specify parameter box_len, an array of lengths of the box in each spatial dimension. The length of box_len must be equal to vec_dim."
                )
                
        
        summed_modes = summate_incompr_zero_vel(
            self.vec_dim, self._cov_sample, self._z_1, self._z_2, pos
        )
        print("\nFinished mode summation!")

        nugget = self.get_nugget(summed_modes.shape)

        return (
            #self.mean_u * e1 #!!! Joshua has commented this out to get zero-velocity fields
            + self.mean_u
            * np.sqrt(self.model.var / self._mode_no)
            * summed_modes
            + nugget
        )


    
class GenericRandVectorFieldMeth(RandMeth):
    r"""RandMeth for incompressible random vector fields.

    Parameters
    ----------
    model : :any:`CovModel`
        covariance model
    mean_velocity : :class:`float`, optional
        the mean velocity in x-direction
    mode_no : :class:`int`, optional
        number of Fourier modes. Default: ``1000``
    vec_dim : :class:`int`, optional
        vector dimension, in case it mismatches the model dimension
    seed : :class:`int` or :any:`None`, optional
        the seed of the random number generator.
        If "None", a random seed is used. Default: :any:`None`
    verbose : :class:`bool`, optional
        State if there should be output during the generation.
        Default: :any:`False`
    sampling : :class:`str`, optional
        Sampling strategy. Either

            * "auto": select best strategy depending on given model
            * "inversion": use inversion method
            * "mcmc": use mcmc sampling

    **kwargs
        Placeholder for keyword-args

    Notes
    -----
    The Randomization method is used to generate isotropic
    spatial incompressible random vector fields characterized
    by a given covariance model. The equation is [Kraichnan1970]_:

    .. math::
       u_i\left(x\right)= \bar{u_i} \delta_{i1} +
       \bar{u_i}\sqrt{\frac{\sigma^{2}}{N}}\cdot
       \sum_{j=1}^{N}p_i(k_{j})\left(
       Z_{1,j}\cdot\cos\left(\left\langle k_{j},x\right\rangle \right)+
       Z_{2,j}\cdot\sin\left(\left\langle k_{j},x\right\rangle \right)
       \right)

    where:

        * :math:`\bar u` : mean velocity in :math:`e_1` direction
        * :math:`N` : fourier mode number
        * :math:`Z_{k,j}` : random samples from a normal distribution
        * :math:`k_j` : samples from the spectral density distribution of
          the covariance model
        * :math:`p_i(k_j) = e_1 - \frac{k_i k_1}{k^2}` : the projector
          ensuring the incompressibility

    References
    ----------
    .. [Kraichnan1970] Kraichnan, R. H.,
           "Diffusion by a random velocity field.",
           The physics of fluids, 13(1), 22-31., (1970)
    """

    def __init__(
        self,
        model,
        mean_velocity=1.0,
        mode_no=1000,
        vec_dim=None,
        seed=None,
        verbose=False,
        sampling="auto",
        **kwargs,
    ):
        if vec_dim is None and (model.dim < 2 or model.dim > 3):
            raise ValueError(
                "Only 2D and 3D incompressible vectors can be generated."
            )
        if vec_dim is not None and (vec_dim < 2 or vec_dim > 3):
            raise ValueError(
                "Only 2D and 3D incompressible vectors can be generated."
            )
        super().__init__(model, mode_no, seed, verbose, sampling, **kwargs)

        self.mean_u = mean_velocity
        if vec_dim is None:
            self.vec_dim = model.dim
        else:
            self.vec_dim = vec_dim
        self._value_type = "vector"

    def __call__(self, pos):
        """Calculate the random modes for the randomization method.

        This method  calls the `summate_incompr_*` Cython methods,
        which are the heart of the randomization method.
        In this class the method contains a projector to
        ensure the incompressibility of the vector field.

        Parameters
        ----------
        pos : (d, n), :class:`numpy.ndarray`
            the position tuple with d dimensions and n points.

        Returns
        -------
        :class:`numpy.ndarray`
            the random modes
        """
        pos = np.asarray(pos, dtype=np.double)
        summed_modes = summate_generic_vector_field(
            self.vec_dim, self._cov_sample, self._z_1, self._z_2, pos
        )
        nugget = self.get_nugget(summed_modes.shape)

        e1 = self._create_unit_vector(summed_modes.shape)

        return (
            #self.mean_u * e1 #!!! Joshua has commented this out to get zero-velocity fields
            + self.mean_u
            * np.sqrt(self.model.var / self._mode_no)
            * summed_modes
            + nugget
        )

    def _create_unit_vector(self, broadcast_shape, axis=0):
        """Create a unit vector.

        Can be multiplied with a vector of shape broadcast_shape

        Parameters
        ----------
        broadcast_shape : :class:`tuple`
            the shape of the array with which
            the unit vector is to be multiplied
        axis : :class:`int`, optional
            the direction of the unit vector. Default: ``0``

        Returns
        -------
        :class:`numpy.ndarray`
            the unit vector
        """
        shape = np.ones(len(broadcast_shape), dtype=int)
        shape[0] = self.vec_dim

        e1 = np.zeros(shape)
        e1[axis] = 1.0
        return e1