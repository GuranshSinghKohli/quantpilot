"""Repository errors. Callers catch these. The database checks stay the backstop."""


class LeagueRepoError(Exception):
    """Base for league repository failures."""


class StatusNotAllowed(LeagueRepoError):
    """create_order and set_order_status refuse this status."""


class StatusTransitionNotAllowed(LeagueRepoError):
    """The order's current status cannot move to the requested status."""


class OrderNotFound(LeagueRepoError):
    """No league.orders row has this id."""


class OrderNotFillable(LeagueRepoError):
    """The order is already filled, or its status is not accepted or queued."""


class FillBeforeOrder(LeagueRepoError):
    """bar_time is at or before the order's game_time."""


class FillNotPositive(LeagueRepoError):
    """Fill price or quantity is not greater than zero."""


class InvalidCommission(LeagueRepoError):
    """Commission is negative."""


class PortfolioNotFound(LeagueRepoError):
    """No league.portfolios row has this id."""


class InsufficientCash(LeagueRepoError):
    """The fill would leave cash below zero."""


class InsufficientPosition(LeagueRepoError):
    """The sell quantity is larger than the held quantity."""
