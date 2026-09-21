import importlib

from .interface import SensorBackend


def create_instance(backend_name: str, *args, **kwargs) -> SensorBackend:
    _module_name, _class_name = ("." + backend_name).rsplit(".", 1)
    _module = importlib.import_module(_module_name, package=__package__)
    cls = getattr(_module, _class_name)
    return cls(*args, **kwargs)
