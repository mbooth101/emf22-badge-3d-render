from app import App
from array import array
from buttons import _num
from math import radians, tan
from micropython import const
from tidal import *
from tidal3d import *
import time

from .buffdisp import BufferedDisplay
from .object import Mesh

MODE_POINT_CLOUD = const(0)
MODE_WIREFRAME_FULL = const(1)
MODE_WIREFRAME_BACK_FACE_CULLING = const(2)
MODE_SOLID = const(3)
MODE_SOLID_SHADED = const(4)


class Renderer(App):

    def __init__(self):
        super().__init__()

        # We'll render the scene to an off-screen buffer and blit it to the display
        # all at once when we're ready
        self.fb = BufferedDisplay(display)

        # Initial render mode and object, see the constants above for other modes
        self.render_mode = MODE_SOLID_SHADED
        self.render_object = 'cube.obj'

        # Projection matrix
        self.m_proj = Renderer.perspective_matrix(90, self.fb.width / self.fb.height,  0.1, 100)

        # Camera position and view transformation matrix
        # TODO a proper camera system
        self.v_campos = [0, 10, 35]
        self.m_view = m_translate(Renderer.identity_matrix(), [0, -10, -35])

        # Lighting vector
        self.v_light = v_normalise([-1,-1,-1])

        # Model to render
        self.mesh = Mesh(self.render_object)

        self.start_t = 0
        self.accum_t = 0
        self.frame_counter = 0
        self.fps = 0

    @staticmethod
    def identity_matrix():
        """
        Returns the identity matrix, multiplication of any matrix M with the identity matrix will yield the
        original matrix M
        """
        return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    @staticmethod
    def perspective_matrix(fov, aspect, near, far):
        """
        Returns the perspective projection matrix for the given field of view and aspect ratio, multiplication
        of any vector V with the projection matrix will yield normalised device coordinates
        """
        proj_mat = [0] * 16
        # Set the field of view, accomodating for the aspect ratio of the screen
        scale = tan(radians(fov * 0.5))
        proj_mat[0] = 1.0 / (scale * aspect)  # Scale of the x coord of the projected vertex
        proj_mat[5] = 1.0 / scale  # Scale of the y coord of the projected vertex
        # Z clipping planes
        proj_mat[10] = -far / (far - near)
        proj_mat[14] = -far * near / (far - near)
        # Setting these following values is how we do the perspective division, by causing w to be set to -z
        # instead of 1 and then w is used as the normalising divisor during vector-proj_mat multiplication
        proj_mat[11] = -1
        proj_mat[15] = 0

        return proj_mat

    def on_activate(self):
        super().on_activate()

        # Register input callbacks
        self.buttons.on_press(BUTTON_A, self.select_mode)
        self.buttons.on_press(BUTTON_B, self.select_object)
        self.buttons.on_press(JOY_UP, self.button_up, False)
        self.buttons.on_press(JOY_DOWN, self.button_down, False)
        self.buttons.on_press(JOY_LEFT, self.button_left, False)
        self.buttons.on_press(JOY_RIGHT, self.button_right, False)

        self.start_t = time.ticks_ms()
        self.loop()

    def on_deactivate(self):
        self.timer.cancel()
        super().on_deactivate()

    def select_mode(self):
        # Cycle through the render modes
        self.render_mode += 1
        if self.render_mode > MODE_SOLID_SHADED:
            self.render_mode = 0

    def select_object(self):
        # Cycle through objects to render
        if self.render_object == 'cube.obj':
            self.render_object = 'teapot.obj'
        elif self.render_object == 'teapot.obj':
            self.render_object = 'cube.obj'
        # Reload the model
        self.mesh = Mesh(self.render_object)

    def button_left(self):
        self.mesh.angular[1] = 45

    def button_right(self):
        self.mesh.angular[1] = -45

    def button_up(self):
        self.mesh.angular[0] = 45

    def button_down(self):
        self.mesh.angular[0] = -45

    def _get_button_state(self, pin):
        # Buttons are active so invert to make truthy mean pressed, which seems more intuitive
        return not self.buttons._callbacks[_num(pin)].state

    def loop(self):
        last_t = self.start_t
        self.start_t = time.ticks_ms()
        delta_t = time.ticks_diff(self.start_t, last_t)

        # Update the simulation
        self.update(delta_t / 1000)

        # Render the scene
        self.render_background()
        self.render_scene()
        self.render_foreground()
        self.fb.blit()

        # Calculate frames per second
        self.frame_counter += 1
        self.accum_t += delta_t
        if self.accum_t > 1000:
            self.accum_t -= 1000
            self.fps = self.frame_counter
            self.frame_counter = 0

        # Show the time it took to render the frame
        print("{} ms".format(delta_t))

        self.timer = self.after(1, self.loop)

    def update(self, delta_t):
        # Kill velocity if buttons no longer pressed
        if not self._get_button_state(JOY_LEFT) and not self._get_button_state(JOY_RIGHT):
            self.mesh.angular[1] = 0
        if not self._get_button_state(JOY_UP) and not self._get_button_state(JOY_DOWN):
            self.mesh.angular[0] = 0

        self.mesh.update(delta_t)

    def render_background(self):
        # Just clear the framebuffer by filling it with a solid colour
        self.fb.rect(0, 0, self.fb.width, self.fb.height, BLACK, True)

        # Show some instructions on screen
        self.fb.text("A = RENDER MODE", 0, 0, WHITE)
        self.fb.text("B = NEXT OBJECT", 0, 10, WHITE)
        self.fb.text("JOY = ROTATE", 0, 20, WHITE)

    def render_scene(self):
        mesh = self.mesh

        # Transform all vertices to their positions in the world by multiplying by the model transformation
        # matrix, which is specific to the mesh being rendered (create world coordinates)
        # Note that translating doesn't mean anything for vectors, so normals are rotated only, and vertices
        # are both rotated and translated
        m_model = m_rotate(Renderer.identity_matrix(), mesh.orientation)
        norms = v_multiply_batch(mesh.normals, m_model)
        m_model = m_translate(m_model, mesh.position)
        verts = v_multiply_batch(mesh.vertices, m_model)

        # Generate a list of faces and their projected vertices for rendering
        faces = []
        projected_verts = [None] * len(verts)
        for indices, norm_index, col_index in mesh.faces:
            # Calculate the point in the centre of the face
            centre = v_average([verts[indices[0]], verts[indices[1]], verts[indices[2]]])

            # Calculate the vector of the direction to the camera from the centre of the face
            camera = v_normalise(v_subtract(self.v_campos, centre))

            # Now we use the dot product to determine if the front of the face is pointing at the
            # camera; if the angle between the normal vector and the camera vector is greater than
            # 90 degrees then we are seeing the back of the face, and if we are culling back faces
            # then we can avoid rendering it
            dot = v_dot(norms[norm_index], camera)
            if (dot < 0 and self.render_mode >= MODE_WIREFRAME_BACK_FACE_CULLING):
                continue

            # Since the face is going to be rendered, let's go ahead and project its vertices
            for index in indices:

                # Since faces can share vertices, and matrix multiplication is expensive, let's not
                # project a vertex more than once, we'll just keep a list of vertices that we've
                # already projected
                if projected_verts[index]:
                    continue

                # Transform the world coorinates into camera coordinates by multiplying by the
                # camera view matrix, allowing it be viewed from the camera's point of view
                vert_cam = v_multiply(verts[index], self.m_view)

                # Project the vertex onto a 2D plane by multiplying by the projection matrix, this
                # yields normalised device coords where all points that lie within the viewable space
                # defined by the field of view are mapped to between -1.0, 1.0
                # The projection matrix multiplication also performs the perspective division, which
                # makes more distant points appear further away by making them closer together on the
                # x and y axes
                vert_ndc = v_multiply(vert_cam, self.m_proj)
                projected_verts[index] = vert_ndc

            # Record the face for rendering along with it's z-depth from the camera, the face's
            # z-depth is the z component of the centre point transformed by the camera view matrix
            depth = v_multiply(centre, self.m_view)[2]
            faces.append((indices, norm_index, col_index, depth))

        # A painter's algorithm; use the face's average depth value to order them from back to front,
        # this ensures far away faces are not drawn on top of near faces
        faces.sort(key=lambda face : face[3])

        # Render faces
        framebuffer = self.fb
        coords = array('h', [0] * 6)
        for indices, norm_index, col_index, _ in faces:

            # If a face's projected vertices all lie outside the viewable space (x or y is more than 1
            # or less then -1) then we can cull it because it will not be seen; if at least one vertex
            # can be seen, we'll render the partial face
            visible = False
            face_verts = [projected_verts[i] for i in indices]
            for face_vert in face_verts:
                if face_vert[0] > -1 and face_vert[0] < 1 and face_vert[1] > -1 and face_vert[1] < 1:
                    visible = True
                    break
            if not visible:
                continue

            # Convert normalised device coordinates (NDCs) to screen coordinates, if an NDC's x and y
            # components both lie between -1 and 1, it will result in a valid on-screen pixel location
            # This is implemented in native code for speed reasons, but it just does this:
            #        x = (v[0] + 1) * 0.5 * width
            #        y = (1 - (v[1] + 1) * 0.5) * height
            # Obviously the y axis here is inverted because screens tend to have the origin 0,0 at the
            # top left and increases towards the bottom
            v_ndc_to_screen(face_verts, coords, framebuffer.width, framebuffer.height)

            colour = WHITE
            if self.render_mode > MODE_POINT_CLOUD and self.render_mode < MODE_SOLID_SHADED:
                # Solid, unshaded colour
                rgb = mesh.colours[col_index]
                colour = color565(rgb[0], rgb[1], rgb[2])
            elif self.render_mode >= MODE_SOLID_SHADED:
                # Scale the color by the angle of incidence of the light vector so a face appears
                # more brightly lit the closer to orthogonal it is, but clamp to a minimum value
                # so unlit faces are not totally invisible, simulating a bit of ambient light
                dot = v_dot(norms[norm_index], self.v_light)
                rgb = v_scale(mesh.colours[col_index], -dot)
                colour = color565(max(int(rgb[0]), 8), max(int(rgb[1]), 8), max(int(rgb[2]), 8))

            # Draw to the framebuffer using screen coordinates
            if self.render_mode == MODE_POINT_CLOUD:
                framebuffer.points(coords, colour)
            elif self.render_mode == MODE_WIREFRAME_FULL or self.render_mode == MODE_WIREFRAME_BACK_FACE_CULLING:
                framebuffer.polygon(coords, colour)
            elif self.render_mode >= MODE_SOLID:
                framebuffer.polygon(coords, colour, True)

    def render_foreground(self):
        self.fb.text("{0:2d} fps".format(self.fps), 0, self.fb.height - 10, WHITE)


# Set the entrypoint for the app launcher
main = Renderer

