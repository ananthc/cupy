from __future__ import print_function
from distutils import ccompiler
from distutils import errors
from distutils import msvccompiler
from distutils import sysconfig
from distutils import unixccompiler
import os
from os import path
import sys

import pkg_resources
import setuptools
from setuptools.command import build_ext
from setuptools.command import sdist

from install import build
from install import utils


required_cython_version = pkg_resources.parse_version('0.26.1')
ignore_cython_versions = [
    pkg_resources.parse_version('0.27.0'),
]

MODULES = [
    {
        'name': 'cuda',
        'file': [
            'cupy.core.core',
            'cupy.core.flags',
            'cupy.core.internal',
            'cupy.cuda.cublas',
            'cupy.cuda.cufft',
            'cupy.cuda.curand',
            'cupy.cuda.cusparse',
            'cupy.cuda.device',
            'cupy.cuda.driver',
            'cupy.cuda.memory',
            'cupy.cuda.memory_hook',
            'cupy.cuda.nvrtc',
            'cupy.cuda.pinned_memory',
            'cupy.cuda.profiler',
            'cupy.cuda.nvtx',
            'cupy.cuda.function',
            'cupy.cuda.stream',
            'cupy.cuda.runtime',
            'cupy.util',
        ],
        'include': [
            'cublas_v2.h',
            'cuda.h',
            'cuda_profiler_api.h',
            'cuda_runtime.h',
            'cufft.h',
            'curand.h',
            'cusparse.h',
            'nvrtc.h',
            'nvToolsExt.h',
        ],
        'libraries': [
            'cublas',
            'cuda',
            'cudart',
            'cufft',
            'curand',
            'cusparse',
            'nvrtc',
            'nvToolsExt',
        ],
        'check_method': build.check_cuda_version,
    },
    {
        'name': 'cudnn',
        'file': [
            'cupy.cuda.cudnn',
            'cupy.cudnn',
        ],
        'include': [
            'cudnn.h',
        ],
        'libraries': [
            'cudnn',
        ],
        'check_method': build.check_cudnn_version,
    },
    {
        'name': 'nccl',
        'file': [
            'cupy.cuda.nccl',
        ],
        'include': [
            'nccl.h',
        ],
        'libraries': [
            'nccl',
        ],
        'check_method': build.check_nccl_version,
    },
    {
        'name': 'cusolver',
        'file': [
            'cupy.cuda.cusolver',
        ],
        'include': [
            'cusolverDn.h',
        ],
        'libraries': [
            'cusolver',
        ],
        'check_method': build.check_cusolver_version,
    },
    {
        # The value of the key 'file' is a list that contains extension names
        # or tuples of an extension name and a list of other souces files
        # required to build the extension such as .cpp files and .cu files.
        #
        #   <extension name> | (<extension name>, a list of <other source>)
        #
        # The extension name is also interpreted as the name of the Cython
        # source file required to build the extension with appending '.pyx'
        # file extension.
        'name': 'thrust',
        'file': [
            ('cupy.cuda.thrust', ['cupy/cuda/cupy_thrust.cu']),
        ],
        'include': [
            'thrust/device_ptr.h',
            'thrust/sequence.h',
            'thrust/sort.h',
        ],
        'libraries': [
            'cudart',
        ],
        'check_method': build.check_cuda_version,
    }
]

if sys.platform == 'win32':
    mod_cuda = MODULES[0]
    mod_cuda['libraries'].remove('nvToolsExt')
    if utils.search_on_path(['nvToolsExt64_1.dll']) is None:
        mod_cuda['file'].remove('cupy.cuda.nvtx')
        mod_cuda['include'].remove('nvToolsExt.h')
        utils.print_warning(
            'Cannot find nvToolsExt. nvtx was disabled.')
    else:
        mod_cuda['libraries'].append('nvToolsExt64_1')


def ensure_module_file(file):
    if isinstance(file, tuple):
        return file
    else:
        return (file, [])


def module_extension_name(file):
    return ensure_module_file(file)[0]


def module_extension_sources(file, use_cython, no_cuda):
    pyx, others = ensure_module_file(file)
    ext = '.pyx' if use_cython else '.cpp'
    pyx = path.join(*pyx.split('.')) + ext

    # If CUDA SDK is not available, remove CUDA C files from extension sources
    # and use stubs defined in header files.
    if no_cuda:
        others1 = []
        for source in others:
            base, ext = os.path.splitext(source)
            if ext == '.cu':
                continue
            others1.append(source)
        others = others1

    return [pyx] + others


def check_readthedocs_environment():
    return os.environ.get('READTHEDOCS', None) == 'True'


def check_library(compiler, includes=(), libraries=(),
                  include_dirs=(), library_dirs=()):

    source = ''.join(['#include <%s>\n' % header for header in includes])
    source += 'int main(int argc, char* argv[]) {return 0;}'
    try:
        # We need to try to build a shared library because distutils
        # uses different option to build an executable and a shared library.
        # Especially when a user build an executable, distutils does not use
        # LDFLAGS environment variable.
        build.build_shlib(compiler, source, libraries,
                          include_dirs, library_dirs)
    except Exception as e:
        print(e)
        return False
    return True


def make_extensions(options, compiler, use_cython):
    """Produce a list of Extension instances which passed to cythonize()."""

    no_cuda = options['no_cuda']
    settings = build.get_compiler_setting()

    include_dirs = settings['include_dirs']

    settings['include_dirs'] = [
        x for x in include_dirs if path.exists(x)]
    settings['library_dirs'] = [
        x for x in settings['library_dirs'] if path.exists(x)]
    if sys.platform != 'win32':
        settings['runtime_library_dirs'] = settings['library_dirs']
    if sys.platform == 'darwin':
        args = settings.setdefault('extra_link_args', [])
        args.append(
            '-Wl,' + ','.join('-rpath,' + p
                              for p in settings['library_dirs']))
        # -rpath is only supported when targetting Mac OS X 10.5 or later
        args.append('-mmacosx-version-min=10.5')

    # This is a workaround for Anaconda.
    # Anaconda installs libstdc++ from GCC 4.8 and it is not compatible
    # with GCC 5's new ABI.
    settings['define_macros'].append(('_GLIBCXX_USE_CXX11_ABI', '0'))

    # In the environment with CUDA 7.5 on Ubuntu 16.04, gcc5.3 does not
    # automatically deal with memcpy because string.h header file has
    # been changed. This is a workaround for that environment.
    # See details in the below discussions:
    # https://github.com/BVLC/caffe/issues/4046
    # https://groups.google.com/forum/#!topic/theano-users/3ihQYiTRG4E
    settings['define_macros'].append(('_FORCE_INLINES', '1'))

    if options['linetrace']:
        settings['define_macros'].append(('CYTHON_TRACE', '1'))
        settings['define_macros'].append(('CYTHON_TRACE_NOGIL', '1'))
    if no_cuda:
        settings['define_macros'].append(('CUPY_NO_CUDA', '1'))

    ret = []
    for module in MODULES:
        print('Include directories:', settings['include_dirs'])
        print('Library directories:', settings['library_dirs'])

        if not no_cuda:
            err = False
            if not check_library(compiler,
                                 includes=module['include'],
                                 include_dirs=settings['include_dirs']):
                utils.print_warning(
                    'Include files not found: %s' % module['include'],
                    'Skip installing %s support' % module['name'],
                    'Check your CFLAGS environment variable')
                err = True
            elif not check_library(compiler,
                                   libraries=module['libraries'],
                                   library_dirs=settings['library_dirs']):
                utils.print_warning(
                    'Cannot link libraries: %s' % module['libraries'],
                    'Skip installing %s support' % module['name'],
                    'Check your LDFLAGS environment variable')
                err = True
            elif('check_method' in module and
                 not module['check_method'](compiler, settings)):
                err = True

            if err:
                if module['name'] == 'cuda':
                    raise Exception('Your CUDA environment is invalid. '
                                    'Please check above error log.')
                else:
                    # Other modules are optional. They are skipped.
                    continue

        s = settings.copy()
        if not no_cuda:
            s['libraries'] = module['libraries']

        if module['name'] == 'cusolver':
            compile_args = s.setdefault('extra_compile_args', [])
            link_args = s.setdefault('extra_link_args', [])
            # openmp is required for cusolver
            if compiler.compiler_type == 'unix' and sys.platform != 'darwin':
                # In mac environment, openmp is not required.
                compile_args.append('-fopenmp')
                link_args.append('-fopenmp')
            elif compiler.compiler_type == 'msvc':
                compile_args.append('/openmp')

        if not no_cuda and module['name'] == 'thrust':
            if build.get_nvcc_path() is None:
                utils.print_warning(
                    'Cannot find nvcc in PATH.',
                    'Skip installing thrust support.')
                continue

        for f in module['file']:
            name = module_extension_name(f)
            sources = module_extension_sources(f, use_cython, no_cuda)
            extension = setuptools.Extension(name, sources, **s)
            ret.append(extension)

    return ret


def parse_args():
    cupy_profile = '--cupy-profile' in sys.argv
    if cupy_profile:
        sys.argv.remove('--cupy-profile')
    cupy_coverage = '--cupy-coverage' in sys.argv
    if cupy_coverage:
        sys.argv.remove('--cupy-coverage')
    no_cuda = '--cupy-no-cuda' in sys.argv
    if no_cuda:
        sys.argv.remove('--cupy-no-cuda')

    arg_options = {
        'profile': cupy_profile,
        'linetrace': cupy_coverage,
        'annotate': cupy_coverage,
        'no_cuda': no_cuda,
    }
    if check_readthedocs_environment():
        arg_options['no_cuda'] = True
    return arg_options


cupy_setup_options = parse_args()
print('Options:', cupy_setup_options)

try:
    import Cython
    import Cython.Build
    cython_version = pkg_resources.parse_version(Cython.__version__)
    cython_available = (
        cython_version >= required_cython_version and
        cython_version not in ignore_cython_versions)
except ImportError:
    cython_available = False


def cythonize(extensions, arg_options):
    directive_keys = ('linetrace', 'profile')
    directives = {key: arg_options[key] for key in directive_keys}

    # Embed signatures for Sphinx documentation.
    directives['embedsignature'] = True

    cythonize_option_keys = ('annotate',)
    cythonize_options = {key: arg_options[key]
                         for key in cythonize_option_keys}

    return Cython.Build.cythonize(
        extensions, verbose=True,
        compiler_directives=directives, **cythonize_options)


def check_extensions(extensions):
    for x in extensions:
        for f in x.sources:
            if not path.isfile(f):
                raise RuntimeError(
                    'Missing file: %s\n' % f +
                    'Please install Cython %s. ' % required_cython_version +
                    'Please also check the version of Cython.\n' +
                    'See ' +
                    'https://docs-cupy.chainer.org/en/stable/install.html')


def get_ext_modules(use_cython=False):
    arg_options = cupy_setup_options

    # We need to call get_config_vars to initialize _config_vars in distutils
    # see #1849
    sysconfig.get_config_vars()
    compiler = ccompiler.new_compiler()
    sysconfig.customize_compiler(compiler)

    extensions = make_extensions(arg_options, compiler, use_cython)

    return extensions


def _nvcc_gencode_options(cuda_version):
    """Returns NVCC GPU code generation options."""

    # The arch_list specifies virtual architectures, such as 'compute_61', and
    # real architectures, such as 'sm_61', for which the CUDA input files are
    # to be compiled.
    #
    # The syntax of an entry of the list is
    #
    #     entry ::= virtual_arch | (virtual_arch, real_arch)
    #
    # where virtual_arch is a string which means a virtual architecture and
    # real_arch is a string which means a real architecture.
    #
    # If a virtual architecture is supplied, NVCC generates a PTX code for the
    # virtual architecture. If a pair of a virtual architecture and a real
    # architecture is supplied, NVCC generates a PTX code for the virtual
    # architecture as well as a cubin code for the real architecture.
    #
    # For example, making NVCC generate a PTX code for 'compute_60' virtual
    # architecture, the arch_list has an entry of 'compute_60'.
    #
    #     arch_list = ['compute_60']
    #
    # For another, making NVCC generate a PTX code for 'compute_61' virtual
    # architecture and a cubin code for 'sm_61' real architecture, the
    # arch_list has an entry of ('compute_61', 'sm_61').
    #
    #     arch_list = [('compute_61', 'sm_61')]

    arch_list = ['compute_30', 'compute_50']
    if cuda_version >= 8000:
        arch_list += ['compute_60']

    options = []
    for arch in arch_list:
        if type(arch) is tuple:
            virtual_arch, real_arch = arch
            options.append('--generate-code=arch={},code={},{}'.format(
                virtual_arch, real_arch, virtual_arch))
        else:
            options.append('--generate-code=arch={},code={}'.format(
                arch, arch))

    if sys.argv == ['setup.py', 'develop']:
        return []
    else:
        return options


class _UnixCCompiler(unixccompiler.UnixCCompiler):
    src_extensions = list(unixccompiler.UnixCCompiler.src_extensions)
    src_extensions.append('.cu')

    def _compile(self, obj, src, ext, cc_args, extra_postargs, pp_opts):
        # For sources other than CUDA C ones, just call the super class method.
        if os.path.splitext(src)[1] != '.cu':
            return unixccompiler.UnixCCompiler._compile(
                self, obj, src, ext, cc_args, extra_postargs, pp_opts)

        # For CUDA C source files, compile them with NVCC.
        _compiler_so = self.compiler_so
        try:
            nvcc_path = build.get_nvcc_path()
            base_opts = build.get_compiler_base_options()
            self.set_executable('compiler_so', nvcc_path)

            cuda_version = build.get_cuda_version()
            postargs = _nvcc_gencode_options(cuda_version) + [
                '-O2', '--compiler-options="-fPIC"']
            print('NVCC options:', postargs)

            return unixccompiler.UnixCCompiler._compile(
                self, obj, src, ext, base_opts + cc_args, postargs, pp_opts)
        finally:
            self.compiler_so = _compiler_so


class _MSVCCompiler(msvccompiler.MSVCCompiler):
    _cu_extensions = ['.cu']

    src_extensions = list(unixccompiler.UnixCCompiler.src_extensions)
    src_extensions.extend(_cu_extensions)

    def _compile_cu(self, sources, output_dir=None, macros=None,
                    include_dirs=None, debug=0, extra_preargs=None,
                    extra_postargs=None, depends=None):
        # Compile CUDA C files, mainly derived from UnixCCompiler._compile().

        macros, objects, extra_postargs, pp_opts, _build = \
            self._setup_compile(output_dir, macros, include_dirs, sources,
                                depends, extra_postargs)

        compiler_so = build.get_nvcc_path()
        cc_args = self._get_cc_args(pp_opts, debug, extra_preargs)
        cuda_version = build.get_cuda_version()
        postargs = _nvcc_gencode_options(cuda_version) + ['-O2']
        postargs += ['-Xcompiler', '/MD']
        print('NVCC options:', postargs)

        for obj in objects:
            try:
                src, ext = _build[obj]
            except KeyError:
                continue
            try:
                self.spawn(compiler_so + cc_args + [src, '-o', obj] + postargs)
            except errors.DistutilsExecError as e:
                raise errors.CompileError(e.message)

        return objects

    def compile(self, sources, **kwargs):
        # Split CUDA C sources and others.
        cu_sources = []
        other_sources = []
        for source in sources:
            if os.path.splitext(source)[1] == '.cu':
                cu_sources.append(source)
            else:
                other_sources.append(source)

        # Compile source files other than CUDA C ones.
        other_objects = msvccompiler.MSVCCompiler.compile(
            self, other_sources, **kwargs)

        # Compile CUDA C sources.
        cu_objects = self._compile_cu(cu_sources, **kwargs)

        # Return compiled object filenames.
        return other_objects + cu_objects


class sdist_with_cython(sdist.sdist):

    """Custom `sdist` command with cyhonizing."""

    def __init__(self, *args, **kwargs):
        if not cython_available:
            raise RuntimeError('Cython is required to make sdist.')
        ext_modules = get_ext_modules(True)  # get .pyx modules
        cythonize(ext_modules, cupy_setup_options)
        sdist.sdist.__init__(self, *args, **kwargs)


class custom_build_ext(build_ext.build_ext):

    """Custom `build_ext` command to include CUDA C source files."""

    def run(self):
        if build.get_nvcc_path() is not None:
            def wrap_new_compiler(func):
                def _wrap_new_compiler(*args, **kwargs):
                    try:
                        return func(*args, **kwargs)
                    except errors.DistutilsPlatformError:
                        if not sys.platform == 'win32':
                            CCompiler = _UnixCCompiler
                        else:
                            CCompiler = _MSVCCompiler
                        return CCompiler(
                            None, kwargs['dry_run'], kwargs['force'])
                return _wrap_new_compiler
            ccompiler.new_compiler = wrap_new_compiler(ccompiler.new_compiler)
            # Intentionally causes DistutilsPlatformError in
            # ccompiler.new_compiler() function to hook.
            self.compiler = 'nvidia'
        if cython_available:
            ext_modules = get_ext_modules(True)  # get .pyx modules
            cythonize(ext_modules, cupy_setup_options)
        check_extensions(self.extensions)
        build_ext.build_ext.run(self)
