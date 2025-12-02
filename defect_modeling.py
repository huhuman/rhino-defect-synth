

def get_reference_points(surface):
    '''
    Params
        surface
    Return 
        List of points, sizes
    '''
    pass

def get_surfaces():
    '''
    Filter surfaces that can place defects
    Return
        List of surface objects
    '''
    pass

def create_curve_from_file(filename):
    pass

def subtract_surface(curves):
    '''
    Subtract the component surface by defect curves (spall only)
    '''
    pass

def modeling_spall(reference_points, reference_sizes):
    # create_curve_from_file()
    # curves = layer-wise smaller areas
    # loft to get transition surfaces
    # combine the bottom surface and loft surfaces -> spall geometry
    # subtract_surface(curves)
    # modeling_rebar()
    pass

def modeling_rebar(left, right, top, bottom):
    pass

def modeling_efflore(reference_points, reference_sizes):
    # should follow similar logic to spall
    # create_curve_from_file()
    # extrude for a very small dist.
    pass

if __name__ == '__main__':
    pass
