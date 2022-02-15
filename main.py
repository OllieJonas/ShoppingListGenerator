import json
import logging
import re
import sys

from dateutil import parser

from todoist_api_python.api import *

possible_shopping_list_names = ["shopping list", "grocery list"]
possible_meal_planning_names = ["meal planning", "meal plan"]

excluded_symbols_regex = 'X|x|❌'

maybe_symbols_regex = '\\?|❓'

days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
meal_times = ["Breakfast", "Lunch", "Dinner"]


class Ingredient:
    def __init__(self, name="", amount=1, measurement="", should_exclude=False, should_include_as_maybe=False):
        self.name = name
        self.amount = amount
        self.measurement = measurement
        self.should_exclude = should_exclude
        self.should_include_as_maybe = should_include_as_maybe

    def __str__(self):
        return f'Ingredient(name={self.name}, ' \
               f'amount={self.amount},' \
               f' measurement=\"{self.measurement}\",' \
               f' should_exclude={self.should_exclude},' \
               f' should_include_as_maybe={self.should_include_as_maybe}' \
               f')'

    def __repr__(self):
        return self.__str__()

    def to_shopping_item(self):
        return ShoppingItem(name=self.name, amount=self.amount, measurement=self.measurement)


class ShoppingItem:
    def __init__(self, name="", amount=1, measurement=""):
        self.name = name
        self.amount = amount
        self.measurement = measurement

    def __str__(self):
        return f'Ingredient(name={self.name}, ' \
               f'amount={self.amount},' \
               f' measurement=\"{self.measurement}\"' \
               f')'

    def __repr__(self):
        return self.__str__()

    def to_pretty_string(self):
        return f'{self.name}' + ((f' ({self.amount}' + (' ' if self.measurement in
                                                               ["bunch", "Bunch", "bunches", "Bunches"] else '')
                                  + f'{self.measurement})') if self.amount >= 0 else '')


# [Monday, Thursday] -> [[Monday, Tuesday, Wednesday], [Thursday, Friday, Saturday, Sunday]]
# [Tuesday, Friday, Sunday] -> [[Tuesday, Wednesday, Thursday], [Friday, Saturday], [Sunday, Monday]]
def get_days_split(shopping_days):
    def day_idx(d):
        return days.index(d)

    def get_shopping(idx):
        return shopping_days[idx % len(shopping_days)]

    if any(d not in days for d in shopping_days):
        raise Exception("invalid day!")

    index = 0
    curr = get_shopping(index)
    nxt = get_shopping(index + 1)

    days_split = []

    shopping_days_len = len(shopping_days)
    days_len = len(days)

    while index < shopping_days_len:
        curr_day_index = day_idx(curr)
        tmp = day_idx(nxt)

        next_day_index = tmp + days_len if tmp <= curr_day_index else tmp

        to_append = days[curr_day_index:next_day_index]

        # append any days leftover after cycling round
        for i in range(max(0, next_day_index - days_len)):
            to_append.append(days[i])

        days_split.append(to_append)

        index += 1
        curr = nxt
        nxt = get_shopping(index + 1)

    return days_split


def create_days_meals_dict(planned_meals_tasks, section_id_to_day_dict):
    meals_dict = {d: [] for d in section_id_to_day_dict.values()}

    for task in planned_meals_tasks:
        split = task.content.split(":")

        if len(split) < 2 or len(split[1].strip()) == 0:
            continue

        name = split[1].strip()
        day = section_id_to_day_dict[task.section_id]
        meals_dict[day].append(name)

    return meals_dict


def create_recipe_ingredients_dict(recipe_tasks):
    recipe_ingredients = {}

    for recipe_task in recipe_tasks:
        name = recipe_task.content.lower()
        ingredients = []

        desc = recipe_task.description.split("\n")

        for line in desc:
            information = line.split(":")

            ingredient_name = information[0].strip()
            amount = -1
            measurement = ''
            should_exclude = False
            should_include_as_maybe = False

            if len(information) > 1:
                amount_str = information[1].strip()

                match = re.match(f'(?i)([0-9]+)?[ ]*(g|ml|kg|l|oz|lb|pound|ounce|grams|bunch|bunches|tbsp|tsp|)?'
                                 f'[ ]*({excluded_symbols_regex}|{maybe_symbols_regex})?', amount_str)

                if match is None:
                    raise Exception("failed match!")

                if match.group(1) is not None:
                    try:
                        amount = int(match.group(1))
                    except ValueError:
                        logging.fatal(f'{ingredient_name}:{amount_str} is an invalid measurement!')

                if match.group(2) is not None:
                    measurement = match.group(2)

                if match.group(3) is not None:
                    if match.group(3) in excluded_symbols_regex:
                        should_exclude = True
                    elif match.group(3) in maybe_symbols_regex:
                        should_include_as_maybe = True

            ingredients.append(Ingredient(
                name=ingredient_name,
                amount=amount,
                measurement=measurement,
                should_exclude=should_exclude,
                should_include_as_maybe=should_include_as_maybe))

        recipe_ingredients[name] = ingredients

    return recipe_ingredients


def create_shopping_lists(days_split, days_meals_dict, recipe_ingredients_dict):
    shopping_lists = {"Maybe": {}}

    def add_to_maybe(ingr: Ingredient, start_date: str):
        date_str = f' ({start_date})'
        if date_str not in ingr.name:
            ingr.name += date_str
        add(shopping_lists["Maybe"], ingr)

    def add(d: dict, ingr: Ingredient):
        if ingr.name in d:
            d[ingr.name].amount += ingr.amount
        else:
            d[ingr.name] = ingr.to_shopping_item()

    for time_period in days_split:
        start = time_period[0]
        items = {}

        for day in time_period:
            meals = days_meals_dict[day]
            for meal in meals:
                recipe = recipe_ingredients_dict[meal.lower()]
                if recipe is None:
                    raise Exception("meal not defined!")
                for ingredient in recipe:
                    if ingredient.should_exclude:
                        continue

                    if ingredient.should_include_as_maybe:
                        add_to_maybe(ingredient, start)
                    else:
                        add(items, ingredient)

        shopping_lists[start] = items
    return shopping_lists


def main():
    """
    Script to automatically generate a shopping list based on your meal plan.

    Limitations: Todoist has an API request limit of 450 requests every 15 minutes. It takes 1 request per deletion of
    item in the previous shopping list and 1 request per addition into the new shopping list. Could potentially run
    into request limiting. Need to look into using their Sync API rather than the REST API.

    To work this script, you need to do the following:

    ===== This Project =====

    - Specify your Todoist API Token as the first argument (found in settings)

    ===== On Todoist =====

    - A project called Meal Planning, containing the following sections:
        - Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday
        - Shopping Trips
        - Recipe Box
    - Inside Mon-Sun you need the following tasks:
        - Breakfast, Lunch, Dinner
    - Inside Shopping Trips you need tasks detailing when you plan on going shopping, with the due dates being the
      days you intend to go.
    - Inside Recipe Box, you need tasks detailing names of recipes, and then in the description containing the
      ingredients you need and the amounts of that ingredient, separated by ":". For example: Eggs : 3. Each ingredient
      should be separated by new lines.
    - If an amount isn't specified, it will default to 1 with no measurement.
    - Measurements are fine to include. For example: Rice: 400g is fine.
    - If a measurement isn't specified, it will default to nothing.
    - You can add a X to the end of an ingredient, to signify to not add this item to the shopping list. For example:
    - Sambal 15ml X
    - You can add a ? to signify you want to add this to a "Maybe" section in the shopping list. For example:
    - Dark Soy Sauce 15ml ?

    ===== BUGS =====
    - decimals don't work
    """
    args = sys.argv[1:]
    if len(args) != 1:
        raise Exception("you need to provide the API token as the only extra argument!")

    token = args[0]
    api = TodoistAPI(token)

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')

    try:
        projects = api.get_projects()

        # get projects
        shopping_list_project = [p for p in projects if p.name.lower() in possible_shopping_list_names][0]
        check_not_null(shopping_list_project, 'shopping list project')

        meal_planning_project = [p for p in projects if p.name.lower() in possible_meal_planning_names][0]
        check_not_null(meal_planning_project, 'meal planning project')

        shopping_list_project_id = shopping_list_project.id
        meal_planning_project_id = meal_planning_project.id

        # get sections + tasks
        all_sections = api.get_sections()
        all_meal_planning_sections = [s for s in all_sections if s.project_id == meal_planning_project_id]
        all_tasks = api.get_tasks()

        # sections in meal planning Mon - Sun
        day_sections = [s for s in all_meal_planning_sections if s.name in days]

        # Shopping Trips section in meal planning
        shopping_trip_section = [s for s in all_meal_planning_sections if "Shopping Trip" in s.name][0]
        check_not_null(shopping_trip_section, 'shopping trip section')

        # Recipe Box section in meal planning
        recipe_box_section = [s for s in all_meal_planning_sections if "Recipe Box" in s.name][0]
        check_not_null(recipe_box_section, 'recipe box section')

        # get all tasks in meal planning section
        meal_planning_tasks = [t for t in all_tasks if
                               t.project_id is not None and
                               t.project_id == meal_planning_project_id]

        shopping_trips_tasks = [t for t in meal_planning_tasks if
                                t.section_id == shopping_trip_section.id]

        # get all tasks in mon-sun sections
        planned_meals_tasks = [t for t in meal_planning_tasks if
                               t.section_id in [s.id for s in
                                                day_sections] and
                               any(meal in t.content for meal in
                                   meal_times)]  # rem subtasks of meals - only want the title

        # dict containing the section id to the day
        section_id_to_day_dict = dict(zip([d.id for d in day_sections], days))

        # the name of meals for each day (eg: Monday: ['Egg Fried Rice', 'Sandwich'])
        # ignores any tasks that dont follow <Meal Time>: <Meal Name>
        days_meals_dict = create_days_meals_dict(planned_meals_tasks, section_id_to_day_dict)
        logging.debug(days_meals_dict)

        recipe_tasks = [t for t in all_tasks if t.section_id == recipe_box_section.id]

        recipe_ingredients_dict = create_recipe_ingredients_dict(recipe_tasks)
        logging.debug(recipe_ingredients_dict)

        # get days from Tasks. eg: [Task(24/1/21), Task(27/1/21)] -> [Monday, Thursday]
        shopping_trip_days = list(map(lambda t: date_to_day(t.due.date), shopping_trips_tasks))
        shopping_trip_days.sort(key=lambda d: day_to_int(d))

        # gets days in between shopping trips
        # eg: [Monday, Thursday] -> [[Monday, Tuesday, Wednesday], [Thursday, Friday, Saturday, Sunday]]
        days_split = get_days_split(shopping_trip_days)
        logging.debug(days_split)

        shopping_lists = create_shopping_lists(days_split, days_meals_dict, recipe_ingredients_dict)
        logging.debug(shopping_lists)

        shopping_list_sections = [s for s in all_sections if s.project_id == shopping_list_project_id]

        # TODO: Use sync API for deleting and creating tasks: https://developer.todoist.com/sync/v8/#overview
        for section_name, items in shopping_lists.items():
            if not any(s.name == section_name for s in shopping_list_sections):
                section = api.add_section(section_name, shopping_list_project_id)
            else:
                section = [s for s in shopping_list_sections if s.name == section_name][0]
                [api.delete_task(task_id=t.id) for t in all_tasks if
                 t.project_id == shopping_list_project_id and
                 t.section_id == section.id]
            #
            for _, item in items.items():
                api.add_task(content=item.to_pretty_string(), project_id=shopping_list_project_id,
                             section_id=section.id)

    except Exception as error:
        logging.fatal(error)


def str_to_day_int(date: str) -> int:
    return day_to_int(date_to_day(date))


def day_to_int(date: str) -> int:
    return days.index(date)


def date_to_day(date: str) -> str:
    return parser.parse(date).strftime("%A")


def pretty_print(dictionary):
    print(json.dumps(dictionary, indent=4, sort_keys=True))


def check_not_null(e, name=""):
    if e is None:
        raise Exception(f'{name} cannot be null!')


if __name__ == "__main__":
    main()
