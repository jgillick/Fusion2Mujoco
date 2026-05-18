import adsk, adsk.fusion
import re


def get_valid_filename(input: str):
    """
    Convert a fusion path string to a string that can be used for a clean
    filename. Convert spaces to underscores, and remove anything that is not an alphanumeric,
    only keep the instance number ("<name>:<instance>") when it is not 1.

    Examples::
        A:1+B:2+C:1  →  A_B2_C
        Leg Group+Foot:1   →  Leg-Group_Foot

    """
    # Split the path components on ":<number>+", since path names can contain "+"
    tokens = re.split(r":(\d+)\+", f"{input}+")
    path_components = []
    for i in range(0, len(tokens) - 1, 2):
        name, instance = tokens[i], tokens[i + 1]
        if instance != "1":
            name = f"{name}-{instance}"
        path_components.append(name)

    # Put it all together
    filename = "_".join(path_components)
    filename = str(filename).strip().replace(" ", "-")
    return re.sub(r"(?u)[^-\w.]", "", filename)


def component_has_bodies(component: adsk.fusion.Component):
    """
    Check if the component has visible bodies

    Args:
    component: adsk.fusion.Component
        the component to check

    Returns:
    bool
        True if the component has bodies, False otherwise
    """
    if component.bRepBodies.count == 0 and component.meshBodies.count == 0:
        return False
    if not component.isBodiesFolderLightBulbOn:
        return False

    # Check if the component has visible bodies
    for body in component.bRepBodies:
        if body.isLightBulbOn:
            return True
    for body in component.meshBodies:
        if body.isLightBulbOn:
            return True

    return False
