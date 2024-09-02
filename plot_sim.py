import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import matplotlib.cm as cm
import matplotlib.animation as animation


def import_txt(file_path, lines_to_skip=3):
    X = []
    Y = []
    with open(file_path, "r") as file:
        for _ in range(lines_to_skip):
            next(file)

        for line in file:
            # ignore timesteps
            if line.startswith("#"):
                continue
            # isolate start (A0100 | B0100) from list of values
            parts = line.split("T=")
            assert (
                len(parts) == 2
            ), f"something went wrong, splitting a line at the = sign resulted in {len(parts)} instead of 2"
            # take values we care about
            values = [
                float(val.strip()) for i, val in enumerate(parts[1].split("|")) if i < 3
            ]
            if line.startswith("A"):
                X.append(values)
            elif line.startswith("B"):
                Y.append(values)
            else:
                raise ValueError(
                    f"lines should start with A or B, but this started with {line[0]}"
                )
        assert len(X) == len(Y), f"X and Y have different lengths, {len(X)} != {len(Y)}"
        X = np.array(
            [tuple(row) for row in X],
            dtype=[("lon", float), ("lat", float), ("alt", float)],
        )
        Y = np.array(
            [tuple(row) for row in Y],
            dtype=[("lon", float), ("lat", float), ("alt", float)],
        )
        return X, Y

def trajectory_plot(position, cmap):
    colors = cmap
    return ax.scatter(
        position["lon"], position["lat"], position["alt"], c=colors, s=3, marker="o"
    ), ax.plot(
        position["lon"], position["lat"], position["alt"], c=colors
    )

path = "control.txt.acmi"
blue, red = import_txt(path)
t = np.linspace(0,1,len(blue))
fig = plt.figure(figsize=(8, 6))
ax = fig.add_subplot(111, projection="3d")
trajectory_plot(blue, "b")
trajectory_plot(red, "r")
ax.set(
    xlabel="Longitude",
    ylabel="Latitude",
    zlabel="Altitude",
    title="3D Trajectory Animation",
)
ax.legend()
plt.show()