import adsk, adsk.fusion


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
