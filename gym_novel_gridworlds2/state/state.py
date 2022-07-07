from typing import List, Optional, Tuple, Mapping
from copy import deepcopy
import numpy as np
import random
from functools import reduce
import json

from gym_novel_gridworlds2.contrib.polycraft.objects.door import Door

from ..object import Object, Entity
from ..utils.item_encoder import SimpleItemEncoder
from .cell import Cell

AIR_STR = "air"

from .exceptions import LocationOccupied, LocationOutOfBound


class State:
    def __init__(
        self,
        rng: np.random.Generator = None,
        map_size: Tuple[int] = None,
        objects: Mapping[str, object] = None,
        map_json: dict = None,
        item_list: Mapping[str, int] = {"air": 0},
        **kwargs
    ):
        """
        Initialization of the State Object.
        """
        if map_json is not None:
            if map_size is None:
                map_size = tuple(map_json.get("map").get("size"))
            if objects is None:
                objects = map_json.get("objects")

        # TODO update
        self.initial_info = {
            "map_size": map_size,
            "objects": objects,
            "item_list": item_list,
            **kwargs,
        }
        self.item_encoder = SimpleItemEncoder(item_list)

        self.walls_list = []
        # to be used to store walls where bedrock overlaps on the map

        # Initialization of the objects
        self._objects: Mapping[str, List[Object]] = {}
        self._map: np.ndarray = np.empty(map_size, dtype="object")
        self._map.fill(None)
        self.rng = rng
        self._step_count = 0

    def make_copy(self):
        return deepcopy(self)

    def get_object_id(self, object_name: str):
        return self.item_encoder.get_create_id(object_name)

    def _ensure_not_none(self, loc: tuple):
        if self._map[loc] is None:
            self._map[loc] = Cell()

    def getSymbol(self, obj, state, canWalkOver=False, facing="NORTH"):
        if obj == "tree":
            if state == "block":
                return "T"
            else:
                return "t"
        elif obj == "air":
            return " "
        elif obj == "bedrock":
            return "X"
        elif obj == "door":
            if canWalkOver == False:
                if state == "block":
                    return "D"
                else:
                    return "d"
            else:
                return " "
        elif obj == "rubber":
            if state == "block":
                return "R"
            else:
                return "r"
        elif obj == "chest":
            if state == "block":
                return "C"
            else:
                return "c"
        elif obj == "agent":
            if facing == "NORTH":
                return "^"
            elif facing == "SOUTH":
                return "v"
            elif facing == "EAST":
                return ">"
            else:
                return "<"
        else:
            return " "

    def mapRepresentation(self):
        res: np.ndarray = np.empty(self.initial_info["map_size"], dtype="object")
        for i in range(self.initial_info["map_size"][0]):
            for j in range(self.initial_info["map_size"][1]):
                obj = self.get_objects_at((i, j))
                if len(obj[0]) != 0:
                    if hasattr(obj[0][0], "canWalkOver"):
                        res[i][j] = self.getSymbol(
                            obj[0][0].type,
                            obj[0][0].state,
                            canWalkOver=obj[0][0].canWalkOver,
                        )
                    else:
                        res[i][j] = self.getSymbol(obj[0][0].type, obj[0][0].state)
                else:
                    res[i][j] = " "
                if len(obj[1]) != 0:
                    if hasattr(obj[1][0], "facing"):
                        res[i][j] = self.getSymbol(
                            obj[1][0].type, obj[1][0].state, facing=obj[1][0].facing
                        )
                    else:
                        res[i][j] = self.getSymbol(obj[1][0].type, obj[1][0].state)
        print(res)

    ############################# ALL BLOCKS #############################
    def place_object(self, object_type: str, ObjectClass=Object, properties: dict = {}):
        """
        Places an object onto the map.
        Returns true if success, false if there was a block there
        """
        # get the object id for use in the object dict
        object_id = self.item_encoder.get_create_id(object_type)
        new_loc = tuple(properties["loc"])

        # sanity check
        try:
            new_loc_obj = self._map[new_loc]
        except IndexError as e:
            raise LocationOutOfBound from e

        # ensure there's a cell at this location
        self._ensure_not_none(new_loc)
        cell: Cell = self._map[new_loc]

        # instanciate object
        if "type" in properties:
            del properties["type"]
        obj = ObjectClass(object_type, **properties)

        # placing object in the map
        success = cell.place_object(obj)
        if not success:
            # map is full, skip the next step and raise an exception.
            raise LocationOccupied

        # placing object in the list
        if object_id not in self._objects:
            self._objects[object_id] = []
        self._objects[object_id].append(obj)

        return obj

    def random_place(self, object_str, count, ObjectClass=Object):
        """
        Randomly place the object in the map

        if there's not enough spots available, all available spots will be filled
        """
        all_available_spots = np.argwhere(self._map == None)
        if count >= all_available_spots.shape[0]:
            count = all_available_spots.shape[0]

        picked_indices = self.rng.choice(
            a=all_available_spots.shape[0], size=count, replace=False
        )
        for index in picked_indices:
            properties = {"loc": tuple(all_available_spots[index])}
            self.place_object(object_str, ObjectClass, properties=properties)

    def init_border(self):
        """
        Initializes a bedrock border surrounding the edges of the map
        """
        for i in range(self.initial_info["map_size"][0]):
            for j in range(self.initial_info["map_size"][1]):
                if i == 0 or j == 0:
                    self.place_object("bedrock", properties={"loc": (i, j)})
                elif i == self.initial_info["map_size"][0] - 1:
                    self.place_object("bedrock", properties={"loc": (i, j)})
                elif j == self.initial_info["map_size"][1] - 1:
                    self.place_object("bedrock", properties={"loc": (i, j)})

    def init_border_multi(self, start, end):
        """
        Given a start and an endpoint,
        initializes a bedrock border surrounding the edges
        """
        overlapping_wall = []
        # only one overlapping wall max, add this wall to the walls list if we place over it
        for i in range(end[0] + 1):
            for j in range(end[1] + 1):
                if i == start[0] or i == end[0] or j == start[1] or j == end[1]:
                    if i >= start[0] and j >= start[1]:
                        if not self.contains_block((i, j)):
                            self.place_object("bedrock", properties={"loc": (i, j)})
                        else:
                            overlapping_wall.append((i, j))

        if len(overlapping_wall) > 0:
            self.walls_list.append(overlapping_wall)

        # start from every edge, place bedrock until bedrock is run into

    def init_doors(self):
        # for every wall, randomly init a door to replace a bedrock there
        for wall in self.walls_list:
            without_borders = wall[1 : len(wall) - 1]
            # don't want to place a door where its inaccessible
            coord = tuple(self.rng.choice(without_borders))
        properties = {"loc": coord}
        self.remove_object("bedrock", coord)
        self.place_object("door", Door, properties=properties)

    def remove_space(self):
        # for every row, and for every col
        # proceed linearly down the row/col and place a bedrock until another bedrock is reached, then terminate
        rows = range(self.initial_info["map_size"][0])
        cols = range(self.initial_info["map_size"][1])
        # this nested for loop iterates through rows
        for i in rows:
            for j in cols:
                if not self.contains_block((i, j)):
                    # place bedrock until another is reached
                    self.place_object("bedrock", properties={"loc": (i, j)})
                else:
                    break
        # this nested for loop iterates through rows backwards
        for i in reversed(rows):
            for j in reversed(cols):
                if not self.contains_block((i, j)):
                    # place bedrock until another is reached
                    self.place_object("bedrock", properties={"loc": (i, j)})
                else:
                    break
        # this nested for loop iterates through cols
        for i in rows:
            for j in cols:
                if not self.contains_block((j, i)):
                    # place bedrock until another is reached
                    self.place_object("bedrock", properties={"loc": (j, i)})
                else:
                    break
        # this nested for loop iterates through cols backwards
        for i in reversed(rows):
            for j in reversed(cols):
                if not self.contains_block((j, i)):
                    # place bedrock until another is reached
                    self.place_object("bedrock", properties={"loc": (j, i)})
                else:
                    break

    def remove_object(self, object_name: str, loc: tuple):
        """
        Removes an object from the map, replacing it with air
        """
        loc = tuple(loc)
        # get the object id for use in the object dict
        object_id = self.item_encoder.get_id(object_name)
        if object_id is not None:
            # assert object_name in self._objects, f"Object {object_name} unknown."
            # won't work as object_name isnt directly comparable to objs
            # assert all(i >= j for i, j in zip(loc, [0] * self._map.ndim)), f"Location "
            obj = None

            try:
                # find the location of the object.
                obj_index = next(
                    i for i, v in enumerate(self._objects[object_id]) if v.loc == loc
                )
                obj = self._objects[object_id][obj_index]

                # remove the object from the list but without freeing.
                self._objects[object_id].pop(obj_index)

            except StopIteration:
                raise ValueError(
                    "Object "
                    + object_name
                    + " at "
                    + str(loc)
                    + " is not found in the list"
                )

            # update the map
            cell: Cell = self._map[loc]
            cell.remove_object(obj)

    def get_objects_of_type(self, object_type: str):
        """
        Gets a list of objects of specific type
        WARNING: Do not modify the locations in the object!!
        """
        type_id = self.item_encoder.get_id(object_type)
        if type_id is not None:
            return self._objects.get(type_id) or []
        else:
            return []

    def get_objects_at(self, loc: tuple):
        """
        Gets all objects at a specific location.
        """
        loc = tuple(loc)
        if self._map[loc] is None:
            return ([], [])
        else:
            return self._map[loc].get_obj_entities()

    def get_object_at(self, loc: tuple):
        """
        LEGACY API:
        Gets an object at a specific location.
            If there are multiple objects, the first non-entity
            object will be returned.
        Returns None if it's not found.

        WARNING: Do not modify the locations
        """
        loc = tuple(loc)
        if self._map[loc] is None:
            return None
        objs = self._map[loc].get_obj_entities()
        if len(objs[0]) > 0:
            return objs[0][0]
        elif len(objs[1]) > 0:
            return objs[1][0]
        return None

    def update_object_loc(self, old_loc: tuple, new_loc: tuple):
        """
        Updates the location of an object.
        #TODO: fix; maybe this should move the OBJECT at the location
        while an update_entity_loc should move the ENTITY at the location
        """
        # notes: this algorithm updates both the agent state and the state.
        old_loc = tuple(old_loc)
        new_loc = tuple(new_loc)
        curr_obj = self.get_object_at(new_loc)
        if curr_obj == None or (
            hasattr(curr_obj, "canWalkOver") and curr_obj.canWalkOver == True
        ):
            objs = self.get_objects_at(old_loc)
            print(objs)
            if len(objs[1]) != 0:
                temp = objs[1][0]
                self._map[old_loc].remove_object(objs[1][0])

                self._ensure_not_none(new_loc)
                self._map[new_loc].place_object(temp)
                temp.loc = new_loc
                return True
            else:
                temp = objs[0][0]
                self._map[old_loc].remove_object(objs[0][0])

                self._ensure_not_none(new_loc)
                self._map[new_loc].place_object(temp)
                temp.loc = new_loc
                return True
        else:
            return False

    def is_full(self, loc: tuple):
        if self._map[loc] is None:
            return (False, False)
        return self._map[loc].is_full()

    def contains_block(self, loc: tuple):
        if self._map[loc] is None:
            return False
        return self._map[loc]._contains_block

    def clear(self):
        """
        Removes all objects from the list and clears the map/item list
        TODO not tested
        """
        # remove all objects:
        for index, obj_id in np.ndenumerate(self._map):
            if obj_id is not None:
                obj = self.get_object_at(index)
                print(obj)
                # print(obj.type, index)
                if obj is not None:
                    self.remove_object(obj.type, index)
        # resets item encoder
        self.item_encoder = SimpleItemEncoder()
        # old version:
        """
        TODO: UNUSABLE FOR NOW, UPDATE NEEDED
        """
        # self.__init__(*self.initial_info)
