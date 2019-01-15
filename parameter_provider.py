import os
import sys
import json
import datetime
import copy
from recordclass import recordclass

CMD_LINE_ARGS = "cmdline"

P = None


def _dict_merge(dict1, dict2):
    """
    Recursively merges dict1 and dict2 such that any values in dict2 override values in dict1
    :param dict1:
    :param dict2:
    :return: resulting merged dictionary
    """
    outdict = dict1.copy()
    for k,v in dict2.items():
        # If dict1 has this key and it's also a dictionary, do a recursive merge
        if k in dict1 and isinstance(dict1[k], dict) and isinstance(v, dict):
            outdict[k] = _dict_merge(dict1[k], v)
        # Otherwise just overwrite the key in dict1
        else:
            outdict[k] = dict2[k]
    return outdict


def _save_json(data, full_path):
    full_path = os.path.expanduser(full_path)
    dirname = os.path.dirname(full_path)
    if not os.path.isdir(dirname):
        os.makedirs(dirname, exist_ok=True)

    with open(full_path, "w") as f:
        json.dump(data, f, indent=4)


def _load_json(full_path):
    full_path = os.path.expanduser(full_path)
    if not os.path.isfile(full_path):
        return None
    with open(full_path, "r") as f:
        ret = json.load(f)
    return ret


def _get_param_server_dir():
    cwd = os.getcwd()
    #pyfile = os.path.realpath(__file__)
    #pydir = os.path.dirname(pyfile)
    return cwd


def _get_past_run_dir(run_name):
    pydir = _get_param_server_dir()
    past_runs_dir = os.path.join(pydir, "past_runs")
    run_dir = os.path.join(past_runs_dir, run_name)
    return run_dir


def _load_params(setup_name):
    pydir = _get_param_server_dir()
    paramsdir = os.path.join(pydir, "run_params")
    paramsname = setup_name + ".json"
    paramsfile = os.path.join(paramsdir, paramsname)
    params = _load_json(paramsfile)

    return params


def _log_experiment_start(run_name, params):
    rundir = _get_past_run_dir(run_name)
    paramsfile = os.path.join(rundir, "params.json")
    _save_json(params, paramsfile)


def _import_include_params(params):
    includes = params.get("@include") or []
    inherited_params = {}
    for include in includes:
        print("Including params:", include)
        incl_params = _load_params(include)
        if incl_params is None:
            raise ValueError("No parameter file include found for: ", include)
        incl_params = _import_include_params(incl_params)
        inherited_params = _dict_merge(inherited_params, incl_params)

    # Overlay the defined parameters on top of the included parameters
    params = _dict_merge(inherited_params, params)

    # Delete the @include tag - it is not a valid python name so it will result in an error when converting to object
    if "@include" in params:
        del params["@include"]
    return params


def _search_crossreference(cref, dict_stack):
    # Start at root
    if cref.startswith("/"):
        return _search_crossreference(cref, dict_stack[:1])

    # Go one level up
    if cref.startswith("../"):
        if len(dict_stack) == 1:
            raise ValueError(f"Error parsing crossreference {cref}. Failed to resolve .., at root of tree")
        else:
            return _search_crossreference(cref[len("../"):], dict_stack[:-1])

    this_level_dict = dict_stack[-1]

    # Go one level down
    if "/" in cref:
        k = cref.split("/")[0]
        if k not in this_level_dict:
            raise ValueError(f"Error parsing crossreference {cref}. No such key: {k}")
        v = this_level_dict[k]
        if not isinstance(v, dict):
            raise ValueError(f"Error parsing crossreference {cref}. Value addressed by {k} is not a dict")
        return _search_crossreference(cref[len(k)+1:], dict_stack + [v])

    # Resolve at this level:
    k = cref
    if k not in this_level_dict:
        raise ValueError(f"Error parsing crossreference {cref}. No such key: {k}")
    else:
        return this_level_dict[k]


def _resolve_crossreferences(d, stack=None):
    if stack is None:
        stack = []

    stack = stack + [d]

    for k, v in d.items():
        if isinstance(v, str):
            n = v.find("@ref:")
            if n == 0:
                value = _search_crossreference(v[5:], stack)
                d[k] = value

        if isinstance(v, dict):
            _resolve_crossreferences(v, stack)

    return d


def dict_to_obj(d):
    """
    Recursively convert dictionary d into nested namedtuples.
    At each recursion level, dictionaries are converted to namedtuples, all other objects retain their type.
    :param d:
    :return:
    """
    d = copy.deepcopy(d)
    # First handle recursion
    for k, v in d.items():
        if isinstance(v, dict):
            d[k] = dict_to_obj(v)

    # Then convert at this level
    d_obj = recordclass('CustomParameters', d.keys())(*d.values())
    return d_obj


def initialize_parameters(setup_name_or_names):
    if setup_name_or_names == CMD_LINE_ARGS:
        assert len(sys.argv) >= 2, "The second command-line argument provided must be the setup name"
        setup_names = sys.argv[1:]
    elif isinstance(setup_name_or_names, str):
        setup_names = [setup_name_or_names]
    elif isinstance(setup_name_or_names[0], str):
        setup_names = setup_name_or_names
    else:
        raise ValueError("setup_name_or_names must be string or iterable of strings")

    merged_params = {}
    for setup_name in setup_names:
        # Load the base configuration
        params = _load_params(setup_name)
        if params is None:
            print("Whoops! Parameters not found for: " + str(setup_name))

        # Load all the included parameters
        params = _import_include_params(params)

        # Merge this set of parameters into the complete set of parameters
        merged_params = _dict_merge(merged_params, params)

    # Resolve cross-references
    merged_params = _resolve_crossreferences(merged_params)

    if "experiment_name" in merged_params:
        run_name = merged_params["run_name"]
    else:
        run_name = "UntitledRun"

    # Convert params dictionary to a python object
    params_obj = dict_to_obj(merged_params)

    # Save for external access
    global CURRENT_PARAMS, CURRENT_RUN, P
    P = params_obj
    CURRENT_PARAMS = merged_params
    CURRENT_RUN = run_name
    _log_experiment_start(run_name, ":".join(setup_names))


def get_stamp():
    stamp = datetime.datetime.now().strftime("%M %d %Y - %H:%M:%S")
    return stamp


def log(string):
    rundir = _get_past_run_dir(CURRENT_RUN)
    logfile = os.path.join(rundir, "log.txt")
    stamp = get_stamp()
    logline = stamp + " " + string
    with open(logfile, "a") as fp:
        fp.write(logline + "\n")


def get_current_parameters_dict():
    global CURRENT_PARAMS
    return CURRENT_PARAMS


def get_parameter(*addr):
    return _get_parameter_from_dict(get_current_parameters_dict(), *addr)


def _get_parameter_from_dict(d, *addr):
    if len(addr) == 0:
        return d
    else:
        if addr[0] not in d:
            print(f"Parameter {addr[0]} not found")
            return None
        return _get_parameter_from_dict(d[addr[0]], addr[1:])


def get_run_name():
    global CURRENT_RUN
    return CURRENT_RUN
