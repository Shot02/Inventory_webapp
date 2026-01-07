from .models import UserNotification

def notifications(request):
    """Add notification counts to all templates"""
    if request.user.is_authenticated:
        return {
            'unread_dashboard_count': UserNotification.get_unread_count(
                request.user, 'dashboard'
            ),
            'unread_debtors_count': UserNotification.get_unread_count(
                request.user, 'debtors'
            ),
            'unread_refunds_count': UserNotification.get_unread_count(
                request.user, 'refunds'
            ),
            'unread_sales_count': UserNotification.get_unread_count(
                request.user, 'sales'
            ),
            'total_unread_count': UserNotification.get_unread_count(request.user),
        }
    return {}