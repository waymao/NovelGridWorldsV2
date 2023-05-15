import numpy as np
import functools
import pygame
from copy import deepcopy
from typing import List
import time

from pettingzoo import AECEnv
from pettingzoo.utils import agent_selector
from pettingzoo.utils import wrappers
from gym.spaces import MultiDiscrete

from gym_novel_gridworlds2.actions.action import PreconditionNotMetError
from gym_novel_gridworlds2.utils.novelty_injection import inject

from ..utils.json_parser import ConfigParser
from ..utils.game_report import create_empty_game_result_file, report_game_result
from ..utils.terminal_colors import bcolors

class NovelGridWorldSequentialEnv(AECEnv):
    metadata = {"render_modes": ["human", "rgb_array", None], "render_fps": 4}

    def __init__(self, config_dict: str, max_time_step: int = 2000, time_limit=5000, run_name=None, enable_render=True, logged_agents=[], generate_csv=False, seed=None):
        """
        Init
        TODO more docs
        """
        ### custom variables environment
        self.run_name = run_name
        self.config_dict = config_dict
        if enable_render:
            self.render_mode = "human"
            pygame.display.set_caption(f"NovelGridWorlds2 - {config_dict.get('filename')}")
        else:
            self.render_mode = None

        self.rng = np.random.default_rng(seed=seed)

        self.json_parser = ConfigParser()
        (
            self.internal_state,
            self.dynamic,
            self.agent_manager,
        ) = self.json_parser.parse_json(json_content=config_dict, rendering=enable_render, rng=self.rng)
        self.enable_render = enable_render
        self.internal_state.env_set_game_over = self._set_game_over

        self.goal_item_to_craft = ""  # TODO add to config
        self.MAX_ITER = max_time_step
        ##### Required properties for the environment
        # Agent lists
        self.possible_agents = self.agent_manager.get_possible_agents()
        self.agent_name_mapping = self.agent_manager.get_agent_name_mapping()

        # The agent is done when it's killed or when the goal is reached.
        self.dones = {key: False for key, a in self.agent_manager.agents.items()}

        # The number of non-environmental agents.
        # Game over when all non-env agents are done.
        # And environmental agents will be automatically terminated
        # when all other active agents are done.
        self.num_active_non_env_agents = self.agent_manager.get_non_env_agent_count()

        # the episode will stop when it exceeds time limit.
        self.initial_time = time.time()
        self.time_limit = time_limit

        # spaces: we get from the agent
        self._action_spaces = {
            key: a.agent.get_action_space()
            for key, a in self.agent_manager.agents.items()
        }
        self._observation_spaces = {
            key: a.agent.get_observation_space(self.internal_state._map.shape, 10)
            for key, a in self.agent_manager.agents.items()
        }

        # used to print header of the step
        self.inited_step = -1

        # initialize the game result file
        self.generate_csv = generate_csv
        if self.generate_csv:
            create_empty_game_result_file(self.run_name)

        # initialize the logged agents set
        self.logged_agents = {*logged_agents}

        # Agent
        self.agents = self.possible_agents[:]
        self.rewards = {agent: 0 for agent in self.agents}
        self._cumulative_rewards = {agent: 0 for agent in self.agents}
        self.dones = {agent: False for agent in self.agents}
        self.infos = {agent: {} for agent in self.agents}
        self.num_active_non_env_agents = self.agent_manager.get_non_env_agent_count()
        self.num_moves = 0
        self._agent_selector = agent_selector(self.agents)
        self.agent_selection = self._agent_selector.next()

    def observe(self, agent_name):
        return self.agent_manager.get_agent(agent_name).agent.get_observation(
            self.internal_state, self.dynamic
        )

    @functools.lru_cache(maxsize=None)
    def observation_space(self, agent):
        # Gym spaces are defined and documented here: https://gym.openai.com/docs/#spaces
        return self._observation_spaces[agent]

    @functools.lru_cache(maxsize=None)
    def action_space(self, agent):
        # Gym spaces are defined and documented here: https://gym.openai.com/docs/#spaces
        return self._action_spaces[agent]

    def _was_done_metadata(self, extra_params, agent):
        """
        Sends a dummy metadata to the agent.
        """
        # The reason is that agent selects an action before
        # the check_done action is called so it will still get a second
        # extra action (after the delay) and we will need to send a message back
        # to the agent.
        metadata = {}
        metadata["goal"] = {
            "goalType": "ITEM",
            "goalAchieved": self.internal_state._goal_achieved,
            "Distribution": "Uninformed",
        }
        metadata["step"] = self.num_moves
        metadata["gameOver"] = True
        metadata["command_result"] = {
            "command": extra_params.get("_command"),
            "argument": extra_params.get("_raw_args") or "",
            "result": "SUCCESS",
            "message": "",
            "stepCost": 0,  # TODO cost
        }
        self.agent_manager.agents[agent].agent.update_metadata(metadata)

    def step(self, action, extra_params={}):
        """
        TAKEN FROM
        https://www.pettingzoo.ml/environment_creation#example-custom-environment

        step(action) takes in an action for the current agent (specified by
        agent_selection) and needs to update
        - rewards
        - _cumulative_rewards (accumulating the rewards)
        - dones
        - infos
        - agent_selection (to the next agent)
        And any internal state used by observe() or render()
        """
        if len(self.logged_agents) > 0 and\
                self.agent_selection == self.agents[0] and self.inited_step < self.num_moves:
            # if we log agent behavior and it's the first agent, print the timestep
            print(f"--------------------- step {self.num_moves} ---------------------")
            self.inited_step = self.num_moves

        # reset rewards for current step ("stepCost")
        self.rewards = {agent: 0 for agent in self.possible_agents}

        agent = self.agent_selection

        if self.dones[self.agent_selection]:
            # handles stepping an agent which is already done
            # accepts a None action for the one agent, and moves the agent_selection to
            # the next done agent,  or if there are no more done agents, to the next live agent
            return self._was_done_step(None)

        # set to be done if the agent is done
        # DELAYED one round
        self.dones[agent] = self._is_agent_done(agent)

        # do the action
        action_set = self.agent_manager.get_agent(agent).action_set
        agent_entity = self.agent_manager.get_agent(agent).entity
        # self.agent_manager.update_agent(agent, self.internal_state)

        if agent_entity.nickname == "main_1":
            self.internal_state.selected_action = action_set.actions[action][0]
        # print(agent_entity.inventory)
        metadata = {}

        step_cost = action_set.actions[action][1].get_step_cost(agent_entity, **extra_params) or 0

        try:
            metadata = action_set.actions[action][1].do_action(
                agent_entity, **extra_params
            )
        except PreconditionNotMetError as e:
            # TODO set an error message
            action_failed = True
            metadata = {
                "command_result": {
                    "command": extra_params.get("_command")
                    or action_set.actions[action][0],
                    "argument": extra_params.get("_raw_args") or "",
                    "result": "FAILED",
                    "message": e.message if hasattr(e, "message") else "",
                    "stepCost": step_cost,  # TODO cost
                }
            }
            pass

        self.rewards[agent] -= step_cost

        # send the metadata of the command execution result
        # to the agent (mostly for use in the socket connection)
        if metadata is None:
            metadata = {}
        if type(metadata) != dict:
            raise Exception("Action metadata must be a dictionary! Check the implementation of the action.")
        
        metadata["goal"] = {
            "goalType": "ITEM",
            "goalAchieved": self.dones[agent] and self.internal_state._goal_achieved,
            "Distribution": "Uninformed",
        }
        metadata["step"] = self.num_moves
        # TODO below is delayed by one step
        metadata["gameOver"] = self.dones[agent]
        if "command_result" not in metadata:
            metadata["command_result"] = {
                "command": extra_params.get("_command")
                or action_set.actions[action][0],
                "argument": extra_params.get("_raw_args") or "",
                "result": "SUCCESS",
                "message": "",
                "stepCost": step_cost,
            }
        self.agent_manager.agents[agent].agent.update_metadata(metadata)

        # TODO only print when verbose
        if agent_entity.nickname in self.logged_agents:
            if metadata["command_result"]["result"] == "SUCCESS":
                colorized_result = bcolors.OKGREEN + metadata["command_result"]["result"] + bcolors.ENDC
            else:
                colorized_result = " " + bcolors.FAIL + metadata["command_result"]["result"] + bcolors.ENDC
            print(
                " [{}] | {:<12}  {:<12} | action_picked: {:<15} [{}]".format(
                    colorized_result,
                    agent,
                    agent_entity.nickname,
                    action_set.actions[action][0],
                    extra_params,
                )
            )

            # print inventory info
            print("             inventory:", agent_entity.inventory)
            if metadata["command_result"]["result"] != "SUCCESS":
                print("Failure Reason:", metadata["command_result"]["message"])

        # the agent which stepped last had its _cumulative_rewards accounted for
        # (because it was returned by last()), so the _cumulative_rewards for this
        # agent should start again at 0
        self._cumulative_rewards[agent] = 0

        # collect reward and do scheduled updates if it is the last agent to act.
        # if the action allows an additional action to be done immediately
        # after it, (like SENSE_ALL in polycraft)
        # then don't update info until the next action is done.
        if (
            self._agent_selector.is_last()
            and not action_set.actions[action][1].allow_additional_action
        ):  
            self._game_over_agent_update()
            self.num_moves += 1
            self.internal_state.time_updates()

        else:
            # necessary so that observe() returns a reasonable observation at all times.
            # no rewards are allocated until both players give an action
            self._clear_rewards()

        # selects the next agent.
        if not action_set.actions[action][1].allow_additional_action or self.dones[agent]:
            self.agent_selection = self._agent_selector.next()
        # Adds .rewards to ._cumulative_rewards
        self._accumulate_rewards()
    

    def _set_game_over(self, goal_achieved=False, delayed_by_one_step=True, notes = ""):
        if self.enable_render:
            print("Episode over. Total time steps:", self.num_moves, "Goal achieved:", goal_achieved)
        if delayed_by_one_step:
            if goal_achieved:
                self.internal_state._goal_achieved = True
            else:
                self.internal_state._given_up = True
        else:
            self.dones = {agent: True for agent in self.possible_agents}

        if self.generate_csv:
            report_game_result(
                output_prefix=self.run_name,
                episode=self.internal_state.episode, 
                total_steps=self.num_moves,
                total_time=time.time() - self.initial_time,
                total_cost=self.rewards['agent_0'],
                success=self.internal_state._goal_achieved,
                notes=notes)


    def _game_over_agent_update(self):
        """
        Checks and sets all agent to be done based if the whole episode is over
        """
        if self.internal_state._given_up:
            return
        # update of done, by setting game_over
        # to test: stepCost and max_step_cost
        if self.time_limit is not None and time.time() - self.initial_time > self.time_limit:
            print("Time limit exceeded")
            self._set_game_over(False, notes="Time limit exceeded")
        elif self.num_active_non_env_agents <= 0:
            self._set_game_over(False, notes="All agents are dead")

        # Updated done if number of moves exceeds limit
        if self.MAX_ITER is not None and self.num_moves >= self.MAX_ITER:
            self._set_game_over(
                goal_achieved=False, 
                delayed_by_one_step=True, notes="Max number of steps exceeded"
            )


    def _is_agent_done(self, agent):
        """
        Returns if the agent is done, if the agent is still in the agent list.
        """
        # agent already done, return true and update nothing
        if agent not in self.dones:
            # agent no longer active, return True
            return True
        elif self.dones[self.agent_selection]:
            # if we already marked as done, return True
            return True
        
        # agent not done but we're marking it done
        if self._cumulative_rewards[agent] >= \
            self.agent_manager.get_agent(agent).max_step_cost:
            # if the total step cost exceeds the max step cost, return True
            self.num_active_non_env_agents -= 1
            return True
        elif self.MAX_ITER is not None and self.num_moves > self.MAX_ITER:
            return True
        elif self.internal_state._given_up or self.internal_state._goal_achieved:
            return True
        return False


    def reset(self, seed=None, return_info=False, options=None):
        """
        Resets the novelty and injects novelty
        """
        if seed is not None:
            # print("Setting seed to", seed)
            self.rng = np.random.default_rng(seed=seed)

        ## injection of novelty
        episode = 0
        if options is not None:
            episode = options.get("episode") or 0

        if str(episode) in (self.config_dict.get("novelties") or {}):
            self.config_dict = inject(
                self.config_dict, self.config_dict["novelties"][str(episode)]
            )

        # initialization
        (
            self.internal_state,
            self.dynamic,
            self.agent_manager,
        ) = self.json_parser.parse_json(
            json_content=self.config_dict, 
            episode=episode,
            rng = self.rng,
            rendering=self.enable_render,
        )
        self.internal_state.env_set_game_over = self._set_game_over

        #### agent novelties
        self.possible_agents = self.agent_manager.get_possible_agents()
        self.agent_name_mapping = self.agent_manager.get_agent_name_mapping()

        # more reset
        self.agents = self.possible_agents[:]
        self.rewards = {agent: 0 for agent in self.agents}
        self._cumulative_rewards = {agent: 0 for agent in self.agents}
        self.dones = {agent: False for agent in self.agents}
        self.infos = {agent: {} for agent in self.agents}
        self.num_active_non_env_agents = self.agent_manager.get_non_env_agent_count()
        self.num_moves = 0

        # Agent
        self._agent_selector = agent_selector(self.agents)
        self.agent_selection = self._agent_selector.next()

        # reset timer
        self.initial_time = time.time()
        self.inited_step = -1
        return self.internal_state._map, None

    def renderTextCenteredAt(self, text, font, colour, x, y, screen, allowed_width):
        # first, split the text into words
        words = text.split()

        # now, construct lines out of these words
        lines = []
        while len(words) > 0:
            # get as many words as will fit within allowed_width
            line_words = []
            while len(words) > 0:
                line_words.append(words.pop(0))
                fw, fh = font.size(" ".join(line_words + words[:1]))
                if fw > allowed_width:
                    break

            # add a line consisting of those words
            line = " ".join(line_words)
            lines.append(line)

        # now we've split our text into lines that fit into the width, actually
        # render them

        # we'll render each line below the last, so we need to keep track of
        # the culmative height of the lines we've rendered so far
        y_offset = 0
        for line in lines:
            fw, fh = font.size(line)

            # (tx, ty) is the top-left of the font surface
            tx = x - fw / 2
            ty = y + y_offset

            font_surface = font.render(line, True, colour)
            screen.blit(font_surface, (tx, ty))

            y_offset += fh

    def render(self, mode="human"):
        if mode != "human":
            return
        
        agent = self.internal_state.get_objects_of_type("agent")[0]
        # agent_obj = self.agent_manager.get_agent(f"agent_{agent.id}")
        self.internal_state.renderer.clear_map()
        self.internal_state.drawMap()
        self.internal_state.renderer.draw_info(
            episode=self.internal_state.episode,
            step_count=self.internal_state._step_count,
            agent_facing=agent.facing,
            selected_action=self.internal_state.selected_action,
            agent_selected_item=agent.selectedItem,
            total_cost=self._cumulative_rewards["agent_" + str(agent.id)],
            agent_inventory=agent.inventory,
            goal_achieved=self.internal_state._goal_achieved,
            given_up=self.internal_state._given_up
        )

    def close(self):
        if hasattr(self, "window") and self.window is not None:
            pygame.display.quit()
            pygame.quit()
